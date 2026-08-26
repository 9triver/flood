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
```

内核围绕九个基本概念工作：

| 概念 | 含义 |
|---|---|
| `Resource` | 可观测、可操作的领域实体，例如水文站、闸门或泵站 |
| `Observation` | 来自基础设施的不可变事实，包含观测时间、接收时间和质量 |
| `Projection` | 由有效观测形成的当前权威状态，乱序或坏数据不能倒退它 |
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
- Domain Event 记录与订阅；
- Agent Intent 提交、风险策略、人工审批和命令状态机；
- Driver 命令下发、基础设施受理和后续遥测确认；
- Observation、Command 和 Domain Event 的 SQLite 持久化；
- 重启时从事实重建 Projection，并继续协调尚未确认的命令；
- 将崩溃时正在下发的命令标为 `outcome_unknown`，禁止盲目重发；
- 基于 MQTT 的水文站 Driver、内存测试 Transport 和 Eclipse Paho 真实 Transport；
- TLS 连接、SUBACK、QoS 1 发布确认、断线重连与自动重订阅；
- 可配置 MQTT topic 前缀，使不同部署和公共 Broker 测试相互隔离；
- 珊瑚河水文站 `808J1510` 的完整示例：调整采样间隔必须审批，并在新遥测报告目标值后才算执行成功。

主要代码边界：

```text
domain_os/                     # 行业无关的最小内核
domain_os/persistence.py       # SQLite durable store
domain_os/transports.py        # 内存与 Eclipse Paho MQTT Transport
domains/flood/domain_system.py # 水利领域装配与 MQTT 水文站 Driver
scripts/check_domain_mqtt.py   # 官方 Broker 真实闭环检查
tests/test_domain_os_runtime.py # 水利纵向闭环验证
server/                        # 既有珊瑚河演示应用，迁移期间继续保留
domains/flood/runtime/         # 既有预测、影响分析和路线规划能力
```

详细设计见 [领域操作系统架构](docs/domain-os/00-产品定义与核心运行模型.md)，设计取舍、验证证据和阶段进度见 [设计思想与研发进展](docs/domain-os/01-设计思想与研发进展.md)。原演示应用的数据集和技术实现见 [既有水利应用技术方案](docs/技术方案.md)。

## 验证新内核

需要 Python 3.11 以上版本及 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
uv run pytest -q tests/test_domain_os_runtime.py
```

运行完整回归：

```bash
uv run pytest -q tests
```

测试使用内存 MQTT Transport，不需要启动 Broker，但走过的 Observation、Intent、Command 和 Reconciliation 路径与真实 Driver 相同。

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

这是概念验证阶段的内核。SQLite Store 用来验证恢复语义，不等同于生产级消息基础设施；官方公共 MQTT 服务只用于连通性验证。系统尚不具备分布式一致性、高可用、租户隔离或完整设备安全能力。下一阶段将把现有水文演进输入和洪水预测能力纳入新内核。
