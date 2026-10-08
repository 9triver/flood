# 基于大模型的水路联动应急智能体集群应用

本项目以珊瑚河流域 GIS 为中心，将边界流量演进、水动力模型预测、淹没影响分析、避洪路线规划和 OAG 智能体交互组织在同一个运行工作空间中。

## 环境准备

需要安装 Git、Git LFS 和 [uv](https://docs.astral.sh/uv/)。首次获取项目时必须同时拉取 `agent` 子模块和水动力模型权重：

```bash
git clone --recurse-submodules git@github.com:9triver/flood.git
cd flood
git lfs install
git lfs pull
uv sync
```

若仓库已经存在，可执行：

```bash
git submodule update --init --recursive
git lfs pull
uv sync
```

## 运行配置

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

仓库内包含运行所需的领域对象库、mock 边界流量、水动力模型网格、配置和 Git LFS 权重。以下内容是本地状态，不进入 Git：

- `local/runtime/flood/`：演进 workspace、预测、路线和可重建缓存。
- `.oag_data/`：Agent 会话与 trace。
- `.env`：LLM 和高德密钥。

`local/runtime/flood/cache/hydrodynamic/mesh.sqlite` 会在首次访问水动力网格时由仓库内的 `GT.txt` 自动重建。

## 验证

```bash
uv run python -m unittest discover -s tests -q
uv run pytest agent/tests -q
node --check server/static/app.js
```

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
