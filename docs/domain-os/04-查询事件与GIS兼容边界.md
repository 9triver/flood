# Domain OS 查询、事件与 GIS 兼容边界

## 1. 目标

本阶段让第一个外部消费者直接读取 Domain OS 的 Projection、DerivedProduct 和 Domain Event，而不把预测或影响评估复制回旧 Workspace。配置领域数据库后，既有 GIS 前端会恢复最新产品并订阅增量事件。

边界分为三层：

```text
DomainRuntime read model
  → DomainQueryService（JSON 查询、全局事件游标、阻塞等待）
  → DomainApi（HTTP/SSE 表示）
  → FloodProductViews（产品 artifact → 既有 GIS DTO）
```

`DomainQueryService` 只依赖领域只读协议，不依赖 OAG、Pi、LLM 客户端或具体 Web 框架。`DomainApi` 负责 SSE 表示，水利兼容层负责理解预测和影响评估产品；行业语义没有进入通用内核。

## 2. 查询与事件契约

当前 HTTP 读取端点如下：

| 端点 | 语义 |
|---|---|
| `GET /api/domain/projections` | 按 `resource_id` 或 `resource_type` 查询当前 Projection |
| `GET /api/domain/products` | 按 `product_type`、`subject_id` 分页查询产品 |
| `GET /api/domain/product?product_id=...` | 查询具体不可变产品 |
| `GET /api/domain/commands` | 按状态、Resource、Actor 或 Capability 查询 Command |
| `GET /api/domain/command?command_id=...` | 查询具体 Command 及原始 Intent |
| `GET /api/domain/events` | 按全局游标、事件类型或主体查询事件 |
| `GET /api/domain/events/stream` | 从 `after` 或 `Last-Event-ID` 继续订阅 SSE |

事件游标是领域事件时间线中的一基全局序号。过滤只决定返回哪些事件，不改变游标含义；因此客户端可以在断线后携带最后一个 SSE `id` 继续读取，不会因为过滤事件而重复扫描旧时间线。

SSE 数据事件名为 `domain_event`，心跳事件名为 `heartbeat`。每条数据都包含 `domain_id`、`cursor`、事件 ID、类型、主体、发生时间、数据和因果字段。

Projection、产品、Command 和事件查询属于只读边界。Intent、批准和拒绝通过独立控制服务进入同一个 DomainRuntime 状态机，详见 [Intent、审批与 Command 接入边界](05-Intent审批与Command接入边界.md)。

## 3. GIS 产品适配

水动力地图继续使用原来的 `meta + tile` DTO，但增加具体 Domain OS 预测产品入口：

```text
GET /api/hydrodynamic-grid/meta?product_id=<forecast-product-id>
GET /api/hydrodynamic-grid/tile?product_id=<forecast-product-id>&z=...&x=...&y=...&time_h=...
```

适配器从预测产品的 `max_depth`、`depth_series` 和 `time_steps` artifact 读取数据，复用共享水动力网格索引生成边界和瓦片。它不会创建 Workspace 预测目录，也不解析 `latest`。降雨序列通过预测产品的具体输入引用读取。

影响面板可以读取具体评估产品：

```text
GET /api/impact-analysis?assessment_product_id=<impact-product-id>
```

也可以用具体预测产品和完整参数查找已经存在的评估产品：

```text
GET /api/impact-analysis?forecast_id=<forecast-product-id>&target_type=all&min_depth_m=0.15&max_distance_m=10
```

该查询不会隐式重算。若所请求的预测时刻和参数没有对应产品，返回 404。默认纵向闭环会产生 24 小时最大包络评估；逐时评估仍需先通过领域 Intent 生成。

不带 Domain OS 产品 ID 的旧地图和影响分析请求继续使用 Workspace，作为迁移期间的兼容路径。OAG 中既有预测、影响分析、路线和应急指令业务工具也继续保留；新增的 Domain OS 工具只负责读取，不把旧工具结果写回新内核。

## 4. 从持久化状态启动

先生成带产品、事件和 Projection 的持久化运行记录：

```bash
uv run python scripts/check_domain_impact.py \
  --database local/runtime/domain-os/water.flood/domain.sqlite
uv run python scripts/check_domain_api.py \
  --database local/runtime/domain-os/water.flood/domain.sqlite
```

第二个命令不会重新执行 CNN；它从持久化记录恢复 Projection、产品和事件，直接读取产品 artifact，检查 GIS 元数据、一个真实瓦片和影响评估 DTO。

再让现有 HTTP 服务恢复该领域运行记录并开放读取接口：

```bash
uv run python server/app.py \
  --host 127.0.0.1 \
  --port 8765 \
  --domain-database local/runtime/domain-os/water.flood/domain.sqlite
```

恢复过程遵循 DomainRuntime 原有语义，包括 Projection 重建和未完成命令协调。HTTP 请求只读取恢复后的内存视图；GIS 直接读取产品记录指向的 artifact。

## 5. OAG 只读工具与事件上下文

配置领域数据库且 OAG 可用时，`FloodApp.attach_domain_runtime()` 会延迟注册四个只读工具：

| 工具 | 语义 |
|---|---|
| `domain_get_projection` | 按 Resource ID 或类型读取当前权威 Projection |
| `domain_list_products` | 分页选择产品，返回具体 ID、有效时间和有界摘要 |
| `domain_get_product` | 按具体产品 ID 读取数据、artifact 名称和完整血缘字段 |
| `domain_list_events` | 从全局游标读取领域事件及 correlation/causation 字段 |

这些工具直接复用 HTTP 层下方的 `DomainQueryService`，避免同进程 HTTP 回环，也不形成第二份 Agent 状态。列表最多返回 50 条；大型数组和字符串转为带总数的预览，产品 ID、类型、主体、生成时间、有效时间、`input_refs`、correlation 和 causation 始终保留。

旧自动事件进入 OAG prompt 前会增加 `domain_os_context`。适配器只解析事件本身已经携带、并且能够从 Domain OS 验证的产品或事件 ID，不按时间猜测关联关系，也不读取 `latest`：

- 找到具体引用时，`linkage=explicit`，上下文只携带 ID、有效时间和血缘元数据；需要内容时由 Agent 调用只读工具。
- 未找到引用时，`linkage=unlinked_legacy_event`，明确禁止把另一个运行的最新产品补配给当前旧事件。

这一处理解决了 OAG 读取和提示上下文的边界问题，但没有把旧自动演进生产链伪装成已经迁移。只有生产侧发出的事件本身带有 Domain OS 具体 ID 时，事件级血缘才真正贯通。

## 6. 当前边界

本阶段已经建立可替换 Agent Runtime 共享的读取与订阅边界，并让 GIS 与 OAG 能够消费同一份只读状态。它尚未完成以下工作：

- 既有自动演进仍由旧运行时生产，尚未改为驱动同一个 DomainRuntime；
- 既有自动事件生产侧尚未携带 Domain OS 具体产品和事件 ID；
- 调用方身份认证、Capability 级授权和多租户隔离尚未实现；
- 时间片评估缺失时不会由 GET 请求触发计算；
- 产品替代、失效、重算和保留策略尚未定义；
- 路线规划与应急指令仍使用旧运行时。

受治理 Intent、审批和 Command API 已在单一事件循环宿主上实现，避免 `ThreadingHTTPServer` 请求线程用临时 `asyncio.run()` 驱动已绑定其他事件循环的 DomainRuntime。下一步应补齐调用方身份认证与 Capability 授权，并统一自动演进生产侧。只有对应生产侧和消费者都切换完成后，才应删除相应 Workspace 状态。控制契约见 [Intent、审批与 Command 接入边界](05-Intent审批与Command接入边界.md)。
