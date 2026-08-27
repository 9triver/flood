# 面向智能体的领域操作系统

本项目是一个 **面向智能体的领域操作系统（Domain OS for Agents）**。

它不是负责对话、提示词或模型会话的 Agent OS，也不是另一个 Agent 框架。它位于 Agent Runtime 与真实业务世界之间：向下持续接入领域基础设施（遥测站、业务系统、模型服务），向上以统一的系统调用接口向任意 Agent Runtime 开放一个可信、可审计、可确认的**领域世界**。

水利防洪是第一个参考领域，用于验证内核能否真实承载“感知—判断—控制—反馈”的业务闭环。内核代码（`dos/`）不含任何水利概念——领域知识全部在设备驱动与进程里。

## 架构

```text
┌─────────────┐  ┌─────────────┐
│ OAG agent    │  │ pi agent    │   …任意 Runtime（各自的用户空间）
└──────┬──────┘  └──────┬──────┘
       │  MCP client     │  MCP client        ← SDK 各语言官方都有
       └────────┬────────┘
                ▼
        dos 守护进程（内核常驻）
        ┌─────────────────────────┐
        │ MCP 网关层（syscall 分发）│  session ↔ capability token 映射
        │                         │  待审批事务 → 通知/审批工具
        ├─────────────────────────┤
        │ kernel: read/watch/act/…│  journal / namespace / 镜像 / fsck / 调度
        └─────────────────────────┘
                ▲
                │ 中断（原始遥测、作业结果、资产库）
        ┌───────┴────────┐
        │ 设备平面        │  MQTT 遥测站 · 计算设备（CNN 预测/影响评估）
        │                │  资产设备（对象库）· 立案设备（agent 判断）
        └────────────────┘
```

## 三条铁律

内核的“操作系统味道”来自三条不变式：

1. **内核不信任用户态**：一切变更经唯一入口 `act()`——冻结检查 → 能力令牌 → 幂等去重 → CAS 前置校验 → journal 记账 → 特权门（人审批）→ 派发。拒绝在记账之前发生，垃圾参数不进 journal。
2. **世界状态只有一份可信副本**：journal（不可变事实，落盘）/ 观测镜像（按世界时间索引的近期历史）/ namespace（当前值 + generation）。智能体只拿到带代次的快照。
3. **发出命令不等于改变现实**：命令开启事务，由**新于派发**的遥测证据经 fsck 裁决 `committed / failed`；超时记 `unknown` 并冻结该路径——绝不盲目重发（重开闸门不是幂等的）。

## 核心概念

| 概念 | OS 角色 | 说明 |
|---|---|---|
| journal | WAL | 只追加的不可变事实日志，一切生效前先记账，全程可回放审计 |
| namespace | 文件系统 + 页缓存 | 世界挂载为路径空间（`/hydro/shanhu/…`）；派生视图按依赖失效、懒重建 |
| 观测镜像 | /proc 累积计数器 | 按世界时间（observed_at）索引的原始观测环；窗口聚合是应用的事 |
| capability | 能力令牌 | 不可伪造令牌是权威唯一通货；令牌不出内核进程 |
| 事务 + fsck | fsck/看门狗 | `open → (awaiting_approval) → dispatched → committed/failed/unknown` |
| 进程 | systemd unit + 调度器 | 确定性业务循环：watches/定时唤醒、优先级、时间预算、失败重启 |
| 计算设备 | 昂贵作业即设备 | CNN 预测、影响评估是设备事务：输入快照入账、产物落盘、命名空间只存句柄 |
| 常驻 agent | 值班工程师 | 进程是条件反射（必办规则），agent 是第一个理解反射结果的人（研判立案） |

## 代码边界

```text
dos/                    # 行业无关内核（零领域概念）
  journal / namespace / history(镜像) / capabilities
  consistency(fsck) / process(调度监督) / kernel(syscall+pump)
  mqtt.py               # 通用遥测站驱动 + 内存/Paho 传输
  asset.py              # 资产设备（参考世界：对象库、几何分流、CRS 保真）
  assessment.py         # 立案设备（agent 判断入世界）+ 看守进程
  gateway.py            # 会话/读域/watch 长轮询/TTL（传输无关）
  mcp_server.py         # MCP 工具面（9 个 syscall 的线上形态）
  persistence.py        # journal JSONL 落盘 + 开机恢复
domains/flood/          # 水利领域（全部领域知识在此）
  dos_instance.py       # 站点装配 + 水位监视进程
  dos_forecast.py       # CNN 计算设备 + 无状态触发进程
  dos_impact.py         # 影响评估设备 + 自动研判进程
  dos_assets.py         # 对象库 loader（16 类 1666 对象）
  runtime/              # 预测、影响分析、网格、路线等领域能力（与内核无关）
server/                 # 演示服务器（dos 为唯一内核）
  dos_host.py           # 内核宿主 + 边界流量回放旋钮
  dos_api.py            # /api/domain/* 与 GIS 视图的 dos 适配
scripts/                # 冒烟：check_dos_{flood,mqtt,forecast,mcp}.py 等
```

## 验证

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
uv run pytest -q tests          # 全量回归（266 项，内核/领域/网关/服务器）
```

分层冒烟（全部无需外部服务，`--real` 除外）：

```bash
uv run python scripts/check_dos_flood.py     # 站点闭环：感知→判断→控制→反馈→审计回放
uv run python scripts/check_dos_mqtt.py      # 真实 MQTT Broker（test.mosquitto.org）闭环
uv run python scripts/check_dos_forecast.py  # 预测/影响全链（--real 走真 CNN，需 git lfs pull）
uv run python scripts/check_dos_mcp.py       # 真实 MCP 客户端跨进程全旅程
```

## 运行演示服务器

```bash
git clone --recurse-submodules git@github.com:9triver/flood.git
cd flood && git lfs install && git lfs pull && uv sync
cp .env.example .env          # 配置 LLM 与高德 Key
uv run python scripts/check_runtime.py --profile full
uv run python server/app.py --host 127.0.0.1 --port 8765
```

服务器以 dos 为唯一内核常驻：边界流量回放驱动遥测 → 无状态触发进程在洪峰越过 230 m³/s 时发起 CNN 预测 → 影响评估自动跟进 → 前端经 `/api/domain/*`（产品、事件 SSE、审批）消费同一份世界状态；重启后世界从 journal 原样恢复。`DOS_FAKE_MODEL=1` 可用即时假模型替换 CNN。

也可以单独启动 MCP 面，用任意 MCP 客户端观察和操作同一个世界：

```bash
uv run python scripts/dos_mcp_server.py      # stdio；DOS_FORECAST=real 换真模型
```

## 设计文档

- [内核设计：操作系统语义](docs/domain-os/内核设计.md) —— 架构判断、纪律与进展的唯一权威文档
- [既有水利应用技术方案](docs/技术方案.md) —— 数据集与原始演示应用（领域侧仍被 dos 设备复用）
- [路线规划](docs/routing.md) —— 高德路线与洪水相交校验

第一代内核（`domain_os/`）的设计文档已随代码一并移除，需要时从 git 历史查阅。

## 当前边界

概念验证阶段内核。已知边界（多数为刻意取舍，详见文档 06）：open_session 信任传输层身份（生产需认证传输）；按主体的速率限制未实现；journal 为单机 JSONL（非分布式）；OAG 对话工具尚待接 MCP 网关；避洪路线与真实影响评估 runner 未迁移。官方公共 MQTT Broker 仅用于连通性验证。不能据此宣称可直接控制生产设施。
