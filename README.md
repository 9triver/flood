# 面向智能体的领域操作系统

本项目是一个 **面向智能体的领域操作系统（Domain OS for Agents）**。

它不是负责对话、提示词或模型会话的 Agent OS，也不是另一个 Agent 框架。它位于 Agent Runtime 与真实业务世界之间：向下持续接入领域基础设施（遥测站、业务系统、模型服务），向上以统一的系统调用接口向任意 Agent Runtime 开放一个可信、可审计、可确认的**领域世界**。

水利防洪是第一个参考领域，用于验证内核能否真实承载“感知—判断—控制—反馈”的业务闭环。内核代码（`dos/`）不含任何水利概念——领域知识全部在设备驱动与进程里。

## 本分支的同步状态

已同步 main 的水利业务：降雨产汇流、原生水库调度引擎、t0 快照与未来 24 小时试算、选中预测帧分析、完整道路几何影响判断，以及配套工作台和智能体工具。水库试算检查安全约束并对比道路影响，结果仍为 `applied=false`，不会切换正式方案或覆盖正式预测。

默认 HTTP 服务运行这套完整业务；`--runtime dos` 保留 OS 实验宿主和 `/api/domain/*` 接口。OS 的影响 runner 仍为模拟实现，其预测输入尚未接入 main 的 t0 未来流量快照，OAG 对话也尚未接入 MCP。因此 OS 演示不能替代默认业务链。下一步是把已有水库状态与试算能力接成 OS 资源和动作。

`dos/` 保留 JSONL 内核及服务器接入；`domain_os_mvp/` 保留独立 SQLite MVP（read/history/watch/act/approve/operation、反馈确认与恢复），尚未挂到 HTTP 服务。这次同步不选择或替换内核。

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
        │ 设备平面        │  MQTT 遥测站 · 计算设备（水动力预测/影响评估）
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
| 计算设备 | 昂贵作业即设备 | 水动力预测、影响评估是设备事务：输入快照入账、产物落盘、命名空间只存句柄 |
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
  dos_forecast.py       # 水动力计算设备 + 无状态触发进程
  dos_impact.py         # 影响评估设备 + 自动研判进程
  dos_assets.py         # 对象库 loader（仓库内当前领域对象）
  runtime/              # 预测、影响分析、网格、路线等领域能力（与内核无关）
server/                 # 水利应用服务器（可选 dos 实验入口）
  dos_host.py           # 内核宿主 + 边界流量回放旋钮
  dos_api.py            # /api/domain/* 与 GIS 视图的 dos 适配
scripts/                # 冒烟：check_dos_{flood,mqtt,forecast,mcp}.py 等
```

## 运行配置

需要 Python 3.11+、Git、Git LFS 和 [uv](https://docs.astral.sh/uv/)。已有仓库更新后执行：

```bash
git submodule update --init --recursive
git lfs pull
uv sync
```

`.env` 不进入 Git。根据 [.env.example](.env.example) 创建本地 `.env`，至少配置：

```dotenv
LLM_API_KEY=your-key
LLM_API_URL=http://your-openai-compatible-service/v1
LLM_MODEL=your-model
AMAP_WEB_SERVICE_KEY=your-amap-web-service-key
```

运行完整系统前可执行检查：

```bash
uv run python scripts/check_runtime.py --profile full
```

只检查不依赖 LLM 和高德密钥的 HTTP/GIS 基础服务：

```bash
uv run python scripts/check_runtime.py --profile server
```

## 启动

```bash
uv run python server/app.py --host 127.0.0.1 --port 8765
```

访问 <http://127.0.0.1:8765>。

服务启动会准备一轮新的空演示；刷新浏览器沿用当前轮次。重启服务或重置演进后，历史输入和预测仍保留在原工作空间，但不会作为当前态势自动启用。开始演进后，预测须匹配当前输入版本和演进时刻才可用于地图、影响分析和避洪规划。

用户对话中的“现在/当前”默认指提交问题时地图时间轴选中的预测帧，没有选中帧时使用演进时钟。同一轮分析及补充回答固定使用该时刻和预测版本，拖动时间轴不会改变进行中的分析。明确指定其他时刻可覆盖默认值；“演进当前时刻”查询演进时钟，“整个预测期/最不利情况”查询最大包络。回答注明实际分析时间及预测偏移；缺失或失效的切片不会被相邻帧、其他版本或最大包络替代。

态势工作台按“业务推演 → 应急响应”组织。宽屏上推演区占约三分之一，展示降雨、流量和调度的关键数据，过程曲线按需展开；响应区占约三分之二，包含“淹没与影响”的研判结果和“应急指令”的处置记录。推演当前时刻、预测分析时刻和指令发出状态分别标注。窄窗口可通过业务分区及响应环节入口切换查看，浮动面板保留原有交互。

地图管理的业务图层按水系与工程、测站、淹没与风险、道路与桥梁、设施与安置、边界与网格折叠展示，默认展开淹没与风险。各行用独立开关显示地图状态，测站和设施可多选叠加；分组计数与“只看已显示”按实际可见类别统计，包括 Agent 或定位操作加载的对象子集。道路共用高速与非高速图例，选中对象详情可从底部摘要展开。网格与预测淹没可独立开关。

## 水库集水区

“业务推演”的三分区降雨、水库调度均提供“定位水库集水区”入口；地图管理“水系与工程”可独立开关范围。`Catchment/longtan_upstream` 保留龙潭水库上游的原始集水边界，关联 `Reservoir/longtan`，面积为 35.4326735909 km²。产流计算和界面控制面积共用该值。数据来源、原始流域边缘差异和重建方法见 [集水区说明](docs/longtan-catchment.md)。

## 道路与路段

- `Road` 保留 423 个独立路段及原始 `road_id`。地图“全部路段”显示全部数据，高速（含匝道）为橙色，非高速为黄色。
- `RoadRoute` 按规范 `Road.ref` 中的 G/S/X/Y 编号聚合，当前有 12 条编号道路，覆盖 184 个路段。地图“编号道路”可选中整组已收录线形，并从详情定位成员路段。
- `RoadRouteSegment` 是多对多关联，当前有 199 条关联。例如 11 个路段同时属于 G65 和 G78。其余 239 个路段缺少可靠编号，继续独立保留，不按同名、邻近或 OSM 要素 ID 自动归属。

道路对象由 `road.jsonl` 动态生成，源数据更新后重建派生对象与地图缓存。稳定 ID 例如 `road_route_G65`；几何是成员的 `MultiLineString`，保留断开线形、双向车道和标号匝道，不强行连接。显示名称优先取单一编号成员的名称众数，保留 `source_names` 供追溯。`recorded_length_m` 是已收录线段长度之和，不是道路全长或里程。

查询示例：`query(RoadRoute, {ref: "G65"})`；成员可通过 `road_ids` 查询 `Road.road_id__in`，或沿 `road_route_segments → road_membership_segment` 关联查询。反向使用 `road_segment_routes → road_membership_route`。

`analyze_inundation_impacts(target_type="RoadRoute")` 逐段执行完整线形与预测湿网格多边形求交后汇总，保留 `segment_impacts` 和受影响路段 ID。所有水深达标的网格进入空间索引，不抽样删减道路顶点或网格。使用模型本地投影 EPSG:4546 计算米制距离与相交长度，支持不连续线形、多部件多边形和孔洞。

`impacts` / `affected_segment_count` 仅计与水深达到阈值（默认 0.15 米）的网格相交的路段；保留 `intersecting_mesh_cell_ids` 和去除重叠后的 `overlap_length_m`。`depth_m` 是相交网格最大预测水深，不能用旁边更深的网格代替。桥上或隧道路段只确认平面相交，返回 `structure_overlap_unverified`，路面高程和通行状态仍需核查。

`max_distance_m` 对道路线对象表示邻近核查距离，默认 10 米，从湿网格边界计算；不扩大受淹判定。仅邻近的对象进入独立的 `nearby_impacts` / `nearby_object_ids`，不计入 `total_impacts`，UI 以“邻近积水，需核查”另列。道路汇总分别返回 `affected_segment_count` 和 `nearby_segment_count`。缺少或无效的几何记录在 `linear_analysis` 中列出，并返回 `status=partial`，不能当成无影响。

未限定对象子集的 `all` 或 `Road` 结果额外提供 `road_route_impacts`、`road_route_nearby_impacts` 与覆盖统计，但不会把道路汇总重复计入 `total_impacts`。界面展示“已收录 N 段中 M 段受影响”，单独说明未归属路段；这些结果不代表整条道路不可通行，也不保证已收录几何覆盖整条道路。

UI 的“影响分析”分为“预测影响”和“邻近积水”页签，支持按对象类型筛选。道路按“编号道路 → 路段”展开，未归属路段单列，共线路段在各所属道路下展示但在对象总数中只计一次；即使工具返回 `RoadRoute` 汇总，UI 对象计数仍使用唯一路段。列表突出相交长度、网格最大预测水深或邻近距离，并标注高程待核查。点击道路汇总只高亮当前页签对应的受影响或邻近路段；仅一段时定位其影响位置，多段时缩放到这些路段的范围，详情仍保留整条道路的汇总。点击单个路段定位相交或邻近位置。预测时间刷新时保留页签、类型筛选、展开状态和滚动位置，并更新选中道路的路段高亮。

## 数据边界

仓库内包含运行所需的领域对象库、mock 降雨及产汇流、调度参数、水动力模型网格、配置和 Git LFS 权重。以下内容是本地状态，不进入 Git：

- `local/runtime/flood/`：演进 workspace、预测、路线和可重建缓存。
- `.oag_data/`：Agent 会话与 trace。
- `.env`：LLM 和高德密钥。

`local/runtime/flood/cache/hydrodynamic/mesh.sqlite` 会在首次访问水动力网格时由仓库内的 `GT.txt` 自动重建。

## 验证

需要 Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
uv run pytest -q tests          # 全量回归（内核/领域/网关/服务器）
```

分层冒烟（MQTT 检查使用公共 Broker；`--real` 使用真实水动力模型）：

```bash
uv run python scripts/check_dos_flood.py     # 站点闭环：感知→判断→控制→反馈→审计回放
uv run python scripts/check_dos_mqtt.py      # 真实 MQTT Broker（test.mosquitto.org）闭环
uv run python scripts/check_dos_forecast.py  # 预测/影响全链（--real 走真实水动力模型，需 git lfs pull）
uv run python scripts/check_dos_mcp.py       # 真实 MCP 客户端跨进程全旅程
uv run python scripts/check_domain_os_mvp.py
uv run python scripts/check_flood_domain_world.py
```

## 运行演示服务器

```bash
git clone --recurse-submodules git@github.com:9triver/flood.git
cd flood && git lfs install && git lfs pull && uv sync
cp .env.example .env          # 首次安装时创建；已有配置请保留
uv run python scripts/check_runtime.py --profile full
uv run python server/app.py --host 127.0.0.1 --port 8765
```

使用 `uv run python server/app.py --runtime dos --port 8766` 启动实验入口：边界流量回放驱动遥测 → 无状态触发进程在洪峰越过 230 m³/s 时发起水动力预测 → 模拟影响评估自动跟进 → 前端经 `/api/domain/*`（产品、事件 SSE、审批）消费同一份世界状态；重启后世界从 journal 原样恢复。`DOS_FAKE_MODEL=1` 可用即时假模型替换水动力模型。

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

三条任务链的端到端测试可自动启动隔离服务，不影响当前演示：

```bash
npm ci --prefix tests/e2e
npm exec --prefix tests/e2e -- playwright install chromium
npm test --prefix tests/e2e
# 使用 .env 中的真实模型验收自然语言及连续指代
npm run test:live --prefix tests/e2e
```

固定场景回归不调用 LLM；真实模型模式调用 LLM。两种模式均使用固定路由响应和预测夹具，覆盖查询筛选、安置点推荐、路线与时间分析、草稿发出及后续复核。详见 [端到端用例说明](tests/e2e/README.md)。原有地图专项回归仍可通过 `NODE_PATH=tests/e2e/node_modules node tests/browser/map-tools.cjs` 在本地服务上运行。

## 智能体运行日志评测材料

`智能体运行日志格式校验工具/智能体运行原生日志生成指南.pdf` 要求最终准备三份文件：

- `traces.json`：静态智能体运行日志，记录真实任务中的模型调用、工具调用和工具结果。
- `traces-dynamic.json`：动态评测器发起 C4/E3 测试后，由系统导出的动态运行日志。
- `information.json`：业务意图、推理任务、工具能力、记忆能力、任务证据和动态 API 配置说明。

本系统在 OAG agent 内使用 OpenTelemetry Python SDK 生成标准 OTLP GenAI trace。默认输出路径是 `.oag_data/genai_traces_flood.json`，也可以通过 `GENAI_TRACE_JSON_PATH` 改到独立文件，便于区分日常 demo、静态评测和动态评测。

推荐流程：

```bash
# 1. 启动系统，并在工作台中完成一组静态业务任务，产生 .oag_data/genai_traces_flood.json。
uv run python server/app.py --host 127.0.0.1 --port 8765

# 2. 生成静态 traces.json 和 information.json。
python3 scripts/build_evaluation_submission.py --refresh-static

# 3. 校验静态 trace 和 information。
uv run python 智能体运行日志格式校验工具/competition-attachments/genai-log-validator/src/main.py \
  --format json local/evaluation-submission/traces.json
uv run python 智能体运行日志格式校验工具/competition-attachments/information-validator/information_validator.py \
  local/evaluation-submission/information.json --json

# 4. 让动态评测器调用本系统的 OpenAI-compatible 入口。
sh 智能体运行日志格式校验工具/competition-attachments/dynamic-evaluation-runner/dynamic-evaluation-runner \
  check local/evaluation-submission/information.json local/evaluation-submission/traces.json
sh 智能体运行日志格式校验工具/competition-attachments/dynamic-evaluation-runner/dynamic-evaluation-runner \
  run local/evaluation-submission/information.json local/evaluation-submission/traces.json \
  --output local/evaluation-submission/execution-report.json --timeout 60

# 5. 根据 execution-report.json 中的 trace_id 过滤生成 traces-dynamic.json。
python3 scripts/build_evaluation_submission.py \
  --dynamic-report local/evaluation-submission/execution-report.json
```

动态评测入口为 `POST /v1/chat/completions`，请求/响应兼容 OpenAI Chat Completions；响应正文和 `traceparent` header 都会携带本次智能体运行的 trace id，供 `traces-dynamic.json` 与执行报告关联。
