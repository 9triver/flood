# Domain OS Intent、审批与 Command 接入边界

## 1. 目标

本阶段在既有只读 Query/SSE 边界上补充受治理写入口，让外部 Agent、人和上层系统提交领域 Intent、查询 Command，并对待审批 Command 作出批准或拒绝决定。HTTP 层不接触 Driver 协议，也不直接修改 Projection 或产品。

```text
HTTP request thread
  → JSON validation / idempotency check
  → DomainRuntimeHost.call()
  → dedicated asyncio event-loop thread
  → DomainControlService
  → DomainRuntime.submit_intent / approve / reject
  → Policy → Command → Driver → Product / later Observation
```

## 2. 单一运行时宿主

`ThreadingHTTPServer` 的请求在多个线程执行。SQLite 默认要求连接只能在创建它的线程使用，async Driver 也可能绑定创建时的事件循环，因此不能在每个请求里调用 `asyncio.run()`。

`server/domain_runtime_host.py` 现在负责：

1. 在专属线程创建 SQLite Store 和水利 DomainSystem。
2. 在该线程的唯一 asyncio loop 中执行 `start()`。
3. 使用 `asyncio.run_coroutine_threadsafe()` 接收 HTTP 线程提交的控制操作。
4. 在同一 loop 中串接 Intent、Policy、Command、Driver 和事件发布。
5. 停服时先执行 DomainSystem `stop()`，再在所属线程关闭 SQLite Store。

只读 Query 通过 `HostedDomainReadModel` 在宿主 loop 中获取 Runtime 的内存快照；所有读写都由同一线程拥有。事件通知跨线程只唤醒 Query 条件变量，不复制领域状态。`wait_for_events()` 在调用宿主代理前释放条件锁，并用事件版本号消除“查询结束到开始等待”之间的丢失唤醒窗口。

## 3. HTTP 契约

| 方法与路径 | 语义 |
|---|---|
| `GET /api/domain/commands` | 按状态、Resource、Actor、Capability 分页查询 Command |
| `GET /api/domain/command?command_id=...` | 查询具体 Command |
| `POST /api/domain/intents` | 提交领域 Intent，由 Policy 决定拒绝、审批或执行 |
| `POST /api/domain/commands/{id}/approve` | 批准 `pending_approval` Command 并进入 Driver 调度 |
| `POST /api/domain/commands/{id}/reject` | 拒绝 `pending_approval` Command 并记录拒绝人和原因 |

Intent 请求示例：

```json
{
  "intent_id": "intent-client-stable-id",
  "actor_id": "agent.flood-operator",
  "resource_id": "water.model/inundation-impact-analyzer",
  "capability_id": "water.flood.analyze-impacts",
  "arguments": {
    "forecast_product_id": "water.flood.forecast/run/000061"
  },
  "rationale": "Assess impacts for the selected immutable forecast",
  "correlation_id": "flood-episode-1"
}
```

客户端若需要安全重试，应提供稳定 `intent_id`。相同 ID 和相同内容返回已有 Command，不重复下发；相同 ID 改变 Actor、目标、Capability、参数、理由或关联 ID 返回 `409 Conflict`。未知字段被拒绝，防止 MQTT topic、HTTP URL 等协议细节越过领域边界。

批准请求包含 `approver_id`。拒绝请求包含 `rejector_id` 和非空 `reason`。只有 `pending_approval` 可以执行这两个转换；对已确认、已拒绝或其他状态重复决策返回 409。

## 4. Command 语义

Command 查询保留完整 Intent、策略原因、Driver ID、审批/拒绝信息、时间、外部 ID、期望反馈、输出和错误。消费者必须区分：

- `pending_approval`：尚未下发。
- `rejected`：Policy 或人工已拒绝，不会下发。
- `dispatching`：正在调用 Driver。
- `acknowledged`：基础设施已受理，但现实状态尚未确认。
- `confirmed`：计算产品已生成，或后续 Observation 已满足期望状态。
- `failed`：Driver 明确失败。
- `outcome_unknown`：中断后无法判断结果，禁止猜测或盲目重发。

OAG 新增 `domain_list_commands` 和 `domain_get_command` 两个只读工具。后台事件上下文若携带可验证的 `command_id`，只注入 Command 引用元数据；完整记录仍通过工具读取。

## 5. 错误与安全边界

HTTP 映射为：请求结构错误或无效领域目标返回 400，记录不存在返回 404，Intent 幂等冲突或非法状态转换返回 409，控制宿主未配置返回 503，宿主调用超时返回 504。

当前接口证明了运行时、线程和状态机边界，但还不是生产控制面：

- `actor_id`、`approver_id` 和 `rejector_id` 仍由调用方声明，尚无身份认证；
- 尚无基于 Resource/Capability 的细粒度授权、租户隔离或双人审批；
- 没有审批有效期、Command 取消、冲突仲裁或人工接管 API；
- HTTP 服务本身尚未提供 TLS、限流、审计导出或防重放令牌；
- OAG 目前只获得 Command 读取工具，通用 Intent 写工具尚未暴露给模型。

因此受治理状态机已经可从外部调用，但部署到真实控制环境前必须先完成身份、授权和网络安全层。

## 6. 验证

自动化测试覆盖 Intent 重试、同 ID 内容冲突、批准、人工拒绝、非法重复决策、Command 过滤查询、事件上下文引用，以及真实 SQLite Store 在专属宿主线程中的创建、写入、停止、关闭和重启恢复。

真实持久化库副本上的 HTTP 检查确认：影响评估 Intent 返回 `confirmed`，复用已有 13 对象评估产品；按 Actor 可查询 Command；改参重试和重复批准均返回 409。验证只写入临时副本，正式检查库未改变。
