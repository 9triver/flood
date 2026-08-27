# Domain OS MVP

这是一个与现有 `dos/` 完全独立的最小实现，用于验证领域操作系统最核心的命题：不可信、可失败的用户态程序能否通过一个持久、可审计的内核，安全完成“观察、判断、操作、反馈确认”的现实闭环。

## 范围

MVP 只实现：

- SQLite append-only Journal；
- 由观测事实形成、可从 Journal 原子重建的当前 World State 投影；
- 带 revision 的 `read`、`history` 和 `watch`；
- 限定资源前缀和动作的能力令牌；
- 唯一副作用入口 `act()`；
- 人工审批、幂等复用和 CAS 前置条件；
- `dispatched` 之后由新观测裁决的操作事务；
- 超时 `unknown` 和资源冻结；
- 监听世界变化的确定性进程；
- 重启后恢复状态与未决操作，但不重新派发命令。

内核本身明确不包含领域本体、MCP、分布式部署、复杂工作流、多 Agent 协议和生产身份认证。这些能力应当建立在稳定 syscall 之上，而不是进入内核原语。

## 领域世界

`domains/flood/` 已安装第一版珊瑚河防洪应急世界：真实 GIS 对象库、MQTT 水文站、水动力模型、高德路线服务、版本化领域产品和确定性应急场景。默认模型与路线 runner 是明确标记的离线替身，也可换成仓库 CNN 和真实高德 Web 服务。

```bash
uv run python scripts/check_flood_domain_world.py
uv run python -m pytest -q tests/test_flood_domain_world.py
```

领域 Agent 使用 `FloodDomainClient`，其对象查询、模型调用、路线规划和研判发布最终都收敛到本 MVP 的六个 syscall。

## 最小闭环

`examples/station.py` 实现一个水位站驱动和水位监视进程：

```text
水位遥测
  -> Journal + World State
  -> 水位监视进程
  -> act(set_sampling_interval)
  -> awaiting_approval
  -> 人工批准
  -> dispatched
  -> 新遥测报告目标采样间隔
  -> committed
```

操作派发之前先持久化 `dispatched_seq`。只有 Journal 序号晚于该栅栏的观测才会交给驱动进行裁决。派发后超时会进入 `unknown` 并冻结资源，内核不会盲目重试。

## 运行

```bash
uv run python scripts/check_domain_os_mvp.py
uv run pytest -q tests/test_domain_os_mvp.py
```

所有代码只使用 Python 标准库。
