# 面向智能体的领域操作系统

本项目正在从单一的水路联动应急智能体应用，演进为一个 **面向智能体的领域操作系统（Domain OS for Agents）**。

它不是负责对话、提示词或模型会话的 Agent OS，也不是另一个 Agent 框架。它位于 Agent Runtime 与真实业务世界之间，持续接入领域基础设施，维护可信的领域状态，并把 Agent 提出的操作意图转换为经过策略约束、可审计、可确认的基础设施命令。

水利防洪是当前第一个参考领域，用于验证这套内核是否能够真实承载“感知—判断—控制—反馈”的业务闭环，而不是内核本身的边界。

## 核心运行模型

```text
领域基础设施
  │ 遥测、状态、告警
  ▼
Driver → Observation → Projection → Domain Event
                                      │
                                      ▼
                               外部 Agent Runtime
                                      │ Intent
                                      ▼
基础设施 ← Driver ← Command ← Policy / Approval
  │                               ▲
  └────── 后续遥测与执行反馈 ──────┘ Reconciliation

确定性领域规则 → Intent → Command → 计算型 Driver
                                      │
                                      ▼
                               DerivedProduct → Domain Event
```

内核围绕十个基本概念工作：

| 概念 | 含义 |
|---|---|
| `Resource` | 可观测、可操作的领域实体，例如水文站、闸门或泵站 |
| `Observation` | 来自基础设施的不可变事实，包含观测时间、接收时间和质量 |
| `Projection` | 由有效观测形成的当前权威状态，乱序或坏数据不能倒退它 |
| `DerivedProduct` | 由事实、预测输入、专业模型或确定性分析生成的不可变派生产物，不冒充现实状态 |
| `Capability` | 资源公开的受治理操作能力及其风险等级 |
| `Intent` | Agent 或人希望达成的领域目标，不是协议命令 |
| `Command` | 经策略决策后交给 Driver 执行的受控操作记录 |
| `Driver` | 领域资源与 MQTT、HTTP、设备 SDK 等基础设施协议之间的适配器 |
| `Policy` | 对身份、能力、风险和人工审批要求作出确定性决策 |
| `Reconciliation` | 用基础设施后续反馈确认命令是否真正改变了现实状态 |

## 当前实现

当前是最小纵向原型，刻意不建设复杂插件框架。代码已经覆盖：

- Driver、Resource 和 Capability 注册以及运行生命周期；
- Observation 留存、幂等去重、质量控制和 Projection 更新；
- 场景 `projection_epoch` 切换、退役 epoch 防回退和 SQLite 重建；
- DerivedProduct 记录、查询、事件发布和 SQLite 恢复；
- Domain Event 记录与订阅；
- Agent Intent 提交、风险策略、人工审批和命令状态机；
- Driver 命令下发、基础设施受理和后续遥测确认；
- Observation、DerivedProduct、Command 和 Domain Event 的 SQLite 持久化；
- 重启时从事实重建 Projection，并继续协调尚未确认的命令；
- 将崩溃时正在下发的命令标为 `outcome_unknown`，禁止盲目重发；
- 基于 MQTT 的水文站 Driver、内存测试 Transport 和 Eclipse Paho 真实 Transport；
- TLS 连接、SUBACK、QoS 1 发布确认、断线重连与自动重订阅；
- 可配置 MQTT topic 前缀，使不同部署和公共 Broker 测试相互隔离；
- 珊瑚河水文站 `808J1510` 的完整示例：调整采样间隔必须审批，并在新遥测报告目标值后才算执行成功。
- 珊瑚河边界流量演进与真实 CNN 的预测闭环：当前数据进入 Observation/Projection，未来 24 小时窗口形成预测输入产品，确定性规则提交 Intent，模型输出版本化预测产品。
- 从具体预测产品到对象影响评估的第二级产品链：显式读取预测产物，确定性叠加对象库，输出带参数签名、对象库版本和预测引用的影响评估产品。

主要代码边界：

```text
domain_os/                     # 行业无关的最小内核
domain_os/persistence.py       # SQLite durable store
domain_os/query.py             # Runtime 无关的只读查询与事件游标
domain_os/control.py           # Runtime 无关的 Intent、批准和拒绝适配
domain_os/transports.py        # 内存与 Eclipse Paho MQTT Transport
domains/flood/domain_system.py # 水利领域装配与 MQTT 水文站 Driver
domains/flood/forecast_domain.py # 演进输入、预测规则与 CNN 计算型 Driver
domains/flood/impact_domain.py # 预测产品到影响评估产品的计算型 Driver
domains/flood/product_views.py # 预测/评估产品到既有 GIS DTO 的适配器
server/domain_tools.py         # OAG 对 Domain OS 查询边界的只读工具适配
server/domain_runtime_host.py  # SQLite 与 async Runtime 的单事件循环宿主
server/domain_playback.py      # 在宿主事件循环上调度领域自动演进
scripts/check_domain_mqtt.py   # 官方 Broker 真实闭环检查
scripts/check_domain_forecast.py # 真实 CSV 与 CNN 预测闭环检查
scripts/check_domain_impact.py # 真实 CNN、网格与对象影响闭环检查
tests/test_domain_os_runtime.py # 水利纵向闭环验证
tests/test_domain_os_forecast.py # 洪水预测纵向闭环验证
tests/test_domain_os_impact.py # 洪水影响评估纵向闭环验证
server/                        # 既有珊瑚河演示应用，迁移期间继续保留
domains/flood/runtime/         # 既有预测、影响分析和路线规划能力
```

详细设计见 [领域操作系统架构](docs/domain-os/00-产品定义与核心运行模型.md)，设计取舍、验证证据和阶段进度见 [设计思想与研发进展](docs/domain-os/01-设计思想与研发进展.md)，纵向实现见 [洪水预测闭环](docs/domain-os/02-洪水预测纵向闭环.md)、[洪水影响评估闭环](docs/domain-os/03-洪水影响评估纵向闭环.md)、[查询、事件与 GIS 兼容边界](docs/domain-os/04-查询事件与GIS兼容边界.md)和[Intent、审批与 Command 接入边界](docs/domain-os/05-Intent审批与Command接入边界.md)。原演示应用的数据集和技术实现见 [既有水利应用技术方案](docs/技术方案.md)。

## 验证新内核

需要 Python 3.11 以上版本及 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
uv run pytest -q tests/test_domain_os_runtime.py tests/test_domain_os_forecast.py tests/test_domain_os_impact.py
```

运行完整回归：

```bash
uv run pytest -q tests
```

测试使用内存 MQTT Transport，不需要启动 Broker，但走过的 Observation、Intent、Command 和 Reconciliation 路径与真实 Driver 相同。

使用仓库中的真实边界流量 CSV 和 CNN 权重检查预测闭环：

```bash
git lfs pull
uv run python scripts/check_domain_forecast.py
```

脚本推进演进数据直到第一次满足预测触发规则，成功时输出输入产品、预测产品、有效时间、模型摘要、产物路径以及 `"command_state": "confirmed"`。

继续验证真实水动力网格与对象影响评估：

```bash
uv run python scripts/check_domain_impact.py
```

该脚本从具体预测产品读取水深文件，完成对象空间叠加并输出分类汇总、少量影响样例和版本化评估产品。

把运行记录持久化后，可由现有 HTTP 服务直接提供 Domain OS 查询、SSE 和 GIS 产品视图：

```bash
uv run python scripts/check_domain_impact.py \
  --database local/runtime/domain-os/water.flood/domain.sqlite
uv run python scripts/check_domain_api.py \
  --database local/runtime/domain-os/water.flood/domain.sqlite
uv run python server/app.py \
  --host 127.0.0.1 \
  --port 8765 \
  --domain-database local/runtime/domain-os/water.flood/domain.sqlite
```

读取接口包括 `/api/domain/projections`、`/api/domain/products`、`/api/domain/commands`、`/api/domain/events` 和 `/api/domain/events/stream`。Intent 与人工决策通过 `POST /api/domain/intents`、`POST /api/domain/commands/{id}/approve` 和 `POST /api/domain/commands/{id}/reject` 进入同一个受治理状态机。SQLite、DomainRuntime 和 async Driver 由专属事件循环线程统一拥有，请求线程不直接运行协程。水动力地图与影响分析接口在携带具体 Domain OS 产品 ID 时直接读取产品 artifact，不复制回旧 Workspace。配置领域数据库后，`/api/autonomy/*` 也会在同一事件循环上调度 `FloodImpactDomainSystem.advance()`；播放 SSE 只承载 UI 状态与边界流量进度，预测和影响结果事件由 `/api/domain/events/stream` 提供。配置领域数据库且 OAG 可用时，智能体会动态获得 Projection、产品、Command 和事件只读工具；工具复用同一个 `DomainQueryService`，不复制领域状态。

## 验证真实 MQTT

可以使用 Eclipse Mosquitto 官方公共测试 Broker 运行完整闭环：

```bash
uv run python scripts/check_domain_mqtt.py
```

脚本默认连接 `test.mosquitto.org:8883`，从官方站点下载测试 CA，并为每次运行生成唯一 topic 前缀。它会完成遥测发布、Projection 更新、Intent 审批、Command 接收和反馈遥测确认，成功时输出 `"command_state": "confirmed"`。

公共 Broker 没有数据隐私、租户隔离和可用性保证，只能发送无敏感信息的测试数据。生产环境必须使用组织控制的 Broker、设备身份和授权策略。

## 运行既有水利演示

迁移阶段不破坏原系统。首次拉取时需要初始化 OAG 子模块和 Git LFS 权重：

```bash
git clone --recurse-submodules git@github.com:9triver/flood.git
cd flood
git lfs install
git lfs pull
uv sync
```

根据 [.env.example](.env.example) 创建 `.env`，配置兼容 OpenAI API 的模型服务和高德 Web 服务密钥，然后启动：

```bash
uv run python scripts/check_runtime.py --profile full
uv run python server/app.py --host 127.0.0.1 --port 8765
```

访问 <http://127.0.0.1:8765>。运行数据写入 `local/runtime/flood/`，Agent 会话与 trace 写入 `.oag_data/`，两者均不进入 Git。

## 当前边界

这是概念验证阶段的内核。SQLite Store 用来验证恢复语义，不等同于生产级消息基础设施；官方公共 MQTT 服务只用于连通性验证。系统尚不具备分布式一致性、高可用、租户隔离或完整设备安全能力。当前完成了洪水预测、影响评估、HTTP/SSE 查询、受治理 Intent/审批接口、GIS 消费者切换、OAG 只读查询适配，以及配置 `--domain-database` 时的自动演进生产侧切换；前端与 OAG 读取同一个 Domain OS 视图。未配置领域数据库时仍回退到旧 `EventRuntime`。外部 Actor/审批人身份尚未认证授权，OAG 写操作、路线规划和应急指令仍使用既有运行时，不能据此宣称原应用已完成迁移或可直接控制生产设施。
