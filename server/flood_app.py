from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

PROJECT_DIR = Path(__file__).resolve().parents[1]
DOMAIN_DIR = PROJECT_DIR / "domains" / "flood"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.map_tools import register_map_tools, tool_result_to_map_event  # noqa: E402
from oag.agent import Agent  # noqa: E402
from oag.harness import Harness  # noqa: E402
from oag.ontology.loader import load_domain  # noqa: E402
from oag.runtime import HarnessConfig  # noqa: E402
from oag.runtime.events import event_to_dict  # noqa: E402
from oag.runtime.hooks import HookResult  # noqa: E402
from domains.flood.runtime.geojson import export_objects_geojson  # noqa: E402
from domains.flood.runtime.hydrodynamic_grid import hydrodynamic_grid_stats, hydrodynamic_grid_tile  # noqa: E402
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts  # noqa: E402
from domains.flood.runtime.tools import list_mappable_objects  # noqa: E402
from domains.flood.runtime.workspace import active_workspace_id  # noqa: E402


ID_FIELDS = {
    "River": "river_id",
    "Watershed": "watershed_id",
    "HydrodynamicBoundary": "boundary_id",
    "County": "county_id",
    "Town": "town_id",
    "Reservoir": "reservoir_id",
    "Sluice": "sluice_id",
    "HydraulicStructure": "structure_id",
    "Road": "road_id",
    "Bridge": "bridge_id",
    "BridgeRoadLink": "bridge_road_link_id",
    "Facility": "facility_id",
    "Place": "place_id",
    "Transfer": "transfer_id",
    "Route": "route_id",
    "Risk": "risk_id",
    "HydroStation": "station_id",
    "ForecastRun": "forecast_id",
    "ForecastCell": "forecast_cell_id",
    "HydrodynamicCell": "hydrodynamic_cell_id",
}

DEFAULT_AGENT_MAX_TURNS = 10
MAX_AGENT_MAX_TURNS = 20
READ_ONLY_AGENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "query_links",
    "describe",
    "pivot",
    "distribution",
    "search",
    "read_tool_result",
})
UI_AGENT_TOOLS = frozenset({
    "ui_show_objects",
    "ui_show_event_marker",
    "ui_clear_map",
    "ui_focus_object",
})
COUNT_OBJECT_HINTS = (
    ("乡镇", "Town", {}),
    ("县级边界", "County", {}),
    ("县", "County", {}),
    ("水库", "Reservoir", {}),
    ("水闸", "Sluice", {}),
    ("桥梁", "Bridge", {}),
    ("桥", "Bridge", {}),
    ("道路", "Road", {}),
    ("学校", "Facility", {"facility_type": "school"}),
    ("医院", "Facility", {"facility_type": "hospital"}),
    ("安置点", "Place", {}),
    ("转移对象", "Transfer", {}),
)


def configured_agent_max_turns(config: dict[str, str]) -> int:
    raw_value = config.get("FLOOD_AGENT_MAX_TURNS", str(DEFAULT_AGENT_MAX_TURNS))
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_AGENT_MAX_TURNS
    return min(MAX_AGENT_MAX_TURNS, max(1, value))


def select_user_agent_tools(message: str, recent_context: str = "") -> frozenset[str]:
    text = str(message or "").lower()
    context_text = str(recent_context or "").lower()
    asks_count = any(keyword in text for keyword in ("几个", "多少个", "数量", "总数"))
    asks_impact = any(keyword in text for keyword in (
        "受影响", "受淹", "淹水影响", "淹没影响", "涉水", "能否通行",
    ))
    asks_route = any(keyword in text for keyword in (
        "路线", "路径", "导航", "绕行", "怎么走", "安置点", "重新规划", "规划一条", "→", "->",
    ))
    asks_forecast = any(keyword in text for keyword in (
        "运行预测", "重新预测", "重算", "重新计算", "更新预测", "启动预测", "未来淹没",
    ))
    asks_cycle = any(keyword in text for keyword in (
        "闭环", "应急循环", "自主观测", "持续预测", "自动告警", "自动调度",
    ))
    asks_map = any(keyword in text for keyword in (
        "地图", "显示", "打开", "画", "绘制", "加载", "叠加", "聚焦", "定位", "缩放", "清空", "隐藏", "不显示",
    ))

    if not asks_route:
        asks_route = any(keyword in context_text for keyword in (
            "路线", "路径", "导航", "绕行", "安置点", "重新规划",
        ))
    if not asks_impact:
        asks_impact = any(keyword in context_text for keyword in (
            "受影响", "受淹", "淹水影响", "淹没影响", "涉水",
        ))

    selected = set(READ_ONLY_AGENT_TOOLS)
    if asks_map or asks_impact or asks_route or asks_forecast or asks_cycle:
        selected.update(UI_AGENT_TOOLS)
    if asks_impact:
        selected.add("analyze_inundation_impacts")
    if asks_route:
        selected.add("plan_evacuation_route")
    if asks_forecast:
        selected.add("run_flood_forecast")
    if asks_cycle:
        selected.add("run_emergency_cycle")
    return frozenset(selected)


def build_agent_task_hint(message: str) -> str:
    text = str(message or "")
    if not any(keyword in text for keyword in ("几个", "多少个", "数量", "总数")):
        return ""
    calls = []
    seen = set()
    for keyword, object_type, filters in COUNT_OBJECT_HINTS:
        if keyword not in text or (object_type, json.dumps(filters, sort_keys=True)) in seen:
            continue
        seen.add((object_type, json.dumps(filters, sort_keys=True)))
        args: dict[str, Any] = {"object_type": object_type}
        if filters:
            args["filters"] = filters
        calls.append(f"count({json.dumps(args, ensure_ascii=False)})")
    if not calls:
        return ""
    return (
        "本问题是领域对象库的直接数量查询。请直接调用 "
        + "、".join(calls)
        + "；不要先查询 River、Watershed、County 或对象几何，也不要添加未在问题中明确给出的过滤条件。"
        "得到 count 结果后立即回答。"
    )


def compact_agent_query_result(raw_result: str) -> str:
    try:
        payload = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return raw_result

    def compact(value: Any) -> Any:
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "geometry":
                result["geometry_available"] = item not in (None, "", {})
                continue
            result[key] = compact(item)
        return result

    return json.dumps(compact(payload), ensure_ascii=False, default=str)


def configure_agent_query_tools(harness: Harness) -> None:
    for tool_name in ("query", "query_links", "search"):
        tool = harness.tools.get(tool_name)
        if not tool:
            continue
        original_handler = tool.handler
        tool.handler = lambda args, handler=original_handler: compact_agent_query_result(handler(args))
        tool.usage_prompt = (
            f"{tool.usage_prompt} 返回结果省略大型 geometry 坐标并用 geometry_available 标记；"
            "对象仍可通过 ui_* 地图工具按完整几何绘制，不要为了获取几何而重复查询。"
        ).strip()
        harness.tools.register(tool)


def load_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


class FloodApp:
    def __init__(self):
        self.llm_config = load_env(PROJECT_DIR / ".env")
        self.llm_client = self._build_llm_client()
        self.ontology, self.repository, self.registry = load_domain(DOMAIN_DIR)
        self.resolver = self.registry.get_resolver("flood_repository")
        self._pending_map_events: dict[str, list[dict[str, Any]]] = {}
        self._pending_map_events_lock = threading.Lock()
        self._pending_forecast_results: dict[str, list[dict[str, Any]]] = {}
        self._pending_forecast_results_lock = threading.Lock()
        self._pending_impact_results: dict[str, list[dict[str, Any]]] = {}
        self._pending_impact_results_lock = threading.Lock()
        self.agent = self._build_agent()
        self._export_lock = threading.Lock()

    @property
    def llm_enabled(self) -> bool:
        return bool(
            self.llm_config.get("LLM_API_URL")
            and self.llm_config.get("LLM_API_KEY")
            and self.llm_config.get("LLM_MODEL")
        )

    def bootstrap(self) -> dict:
        mappable = list_mappable_objects(self.resolver)
        return {
            "domain": self.ontology.name,
            "title": "珊瑚河洪水应急预警智能体",
            "mappable": mappable,
            "counts": {
                "school": self.resolver.count("Facility", {"facility_type": "school"}),
                "hospital": self.resolver.count("Facility", {"facility_type": "hospital"}),
                "government": self.resolver.count("Facility", {"facility_type": "government"}),
            },
            "llm_enabled": self.llm_enabled,
            "default_context": "基础态 · 领域对象地图",
            "workspace_id": active_workspace_id(),
        }

    def autonomy_cycle(self, force_forecast: bool = False) -> dict:
        return self.registry.call("run_emergency_cycle", force_forecast=force_forecast)

    def forecast(self, force: bool = False) -> dict:
        return self.registry.call("run_flood_forecast", forecast_id="latest", force=force)

    def export_geojson(self, object_type: str, filters: dict,
                       simplify: float = 0) -> tuple[dict, bytes]:
        with self._export_lock:
            result = export_objects_geojson(self.resolver, object_type, filters, simplify, force=False)
            if "error" in result:
                raise ValueError(result["error"])
            path = Path(result["absolute_path"])
            return result, path.read_bytes()

    def hydrodynamic_grid_stats(self, forecast_id: str = "latest") -> dict[str, Any]:
        return hydrodynamic_grid_stats(forecast_id)

    def hydrodynamic_grid_tile(self, z: int, x: int, y: int,
                               forecast_id: str = "latest",
                               wet_only: bool = False,
                               time_h: float | None = None,
                               tile_crs: str = "wgs84") -> dict[str, Any]:
        return hydrodynamic_grid_tile(z, x, y, forecast_id, wet_only, time_h, tile_crs)

    def analyze_inundation_impacts(self, forecast_id: str = "latest",
                                   target_type: str = "all",
                                   min_depth_m: float = 0.15,
                                   max_distance_m: float = 10.0,
                                   time_h: float | None = None) -> dict[str, Any]:
        return analyze_inundation_impacts(
            self.resolver,
            forecast_id=forecast_id,
            target_type=target_type,
            min_depth_m=min_depth_m,
            max_distance_m=max_distance_m,
            time_h=time_h,
        )

    def get_object(self, object_type: str, object_id: str) -> dict:
        row = self.resolver.query_by_id(object_type, object_id)
        if row:
            return {"object_type": object_type, "object": row}
        id_field = ID_FIELDS.get(object_type)
        rows = self.resolver.query(object_type, {id_field: object_id}, limit=1) if id_field else []
        return {"object_type": object_type, "object": rows[0] if rows else None}

    def stream_chat(self, run: "AgentRun"):
        selected = run.selected or {}

        if not self.agent:
            self._append_event(run, "text", {
                "type": "text",
                "content": "当前未启用 LLM，无法由智能体推理并调用地图工具。",
            })
            return

        agent_message = self._agent_message(run.message, selected)
        agent_session_id = self.agent_session_id(run.session_id)
        recent_user_context = "\n".join(
            item.get("content", "")
            for item in self.agent.get_history(agent_session_id)[-6:]
            if item.get("role") == "user"
        )
        allowed_tools = select_user_agent_tools(run.message, recent_user_context)
        try:
            for event in self.agent.chat_stream(
                agent_message,
                session_id=agent_session_id,
                allowed_tools=allowed_tools,
            ):
                if run.cancelled:
                    break
                for map_event in self._pop_pending_map_events(agent_session_id):
                    self._append_map_event(run, map_event)
                data = event_to_dict(event)
                self._append_event(run, data["type"], data)
            for map_event in self._pop_pending_map_events(agent_session_id):
                self._append_map_event(run, map_event)
        except Exception as exc:
            print(f"OAG agent stream failed: {exc}")
            self._append_event(run, "text", {
                "type": "text",
                "content": f"智能体生成失败：{exc}",
            })

    def _append_map_event(self, run: "AgentRun", result: dict):
        self._append_event(run, "map_actions", {
            "type": "map_actions",
            "context": result.get("context"),
            "map_actions": result.get("map_actions", []),
            "result_cards": result.get("result_cards", []),
            "llm_enabled": bool(self.agent),
        })

    @staticmethod
    def _append_event(run: "AgentRun", event_type: str, data: dict[str, Any]):
        with run.condition:
            run.seq += 1
            item = {
                "seq": run.seq,
                "type": event_type,
                "data": {**data, "seq": run.seq, "run_id": run.run_id},
            }
            run.events.append(item)
            run.updated_at = time.time()
            run.condition.notify_all()

    def _build_llm_client(self) -> OpenAI | None:
        if not self.llm_enabled:
            return None
        api_url = self.llm_config["LLM_API_URL"].rstrip("/")
        base_url = api_url.removesuffix("/chat/completions").removesuffix("/v1")
        return OpenAI(
            api_key=self.llm_config["LLM_API_KEY"],
            base_url=f"{base_url}/v1",
            timeout=45,
        )

    def _build_agent(self) -> Agent | None:
        if not self.llm_client:
            return None
        harness = Harness(
            ontology=self.ontology,
            repository=self.repository,
            registry=self.registry,
            llm_client=self.llm_client,
            model=self.llm_config["LLM_MODEL"],
            config=HarnessConfig(
                max_turns=configured_agent_max_turns(self.llm_config),
                enable_write_confirmation=True,
                llm_extra_body=self._llm_extra_body(),
                runtime_context={
                    "frontend": "GIS-centered flood emergency workspace",
                    "map_rendering": "Frontend renders domain objects by their geometry. Layer is UI state, not a domain object.",
                },
                append_system_prompt=(
                    "你服务于一个以珊瑚河流域 GIS 为中心的前端。"
                    "回答用户时优先调用领域对象查询和领域函数获取事实。"
                    "当前系统只展示实时预测结果，不提供设计洪水方案切换。"
                    "用户询问领域对象库中有几个、多少个某类基础对象时，直接调用 count；"
                    "不要先查询 River、Watershed 或对象完整几何来建立不必要的空间关系。"
                    "当用户要求在地图上显示、打开、绘制、加载、叠加、缩放、聚焦或清空对象时，"
                    "必须调用 ui_show_objects、ui_show_event_marker、ui_focus_object 或 ui_clear_map，让前端执行地图动作；"
                    "当用户要求清除、不显示或隐藏淹没范围、预测淹没结果、水动力结果时，调用 ui_clear_map 并传 target=inundation；这只移除淹没结果，不改变地图视野。"
                    "不要只用文字说明将要显示什么。"
                    "当用户要求基于预测淹没范围判断学校、医院、道路、桥梁、转移路线或安置点是否受影响时，"
                    "必须调用 analyze_inundation_impacts；不要自行猜测对象级受淹结论。"
                    "如果已有水动力时间轴，而用户只是询问当前时刻哪些对象受影响，直接调用 analyze_inundation_impacts；"
                    "只有用户明确要求运行、重算或更新预测时才调用 run_flood_forecast。"
                    "如果前端上下文 hydrodynamic_timeline.mode=time_slice，且用户询问当前时刻/当前画面/该时刻影响，"
                    "调用 analyze_inundation_impacts 时必须传 current_hydrodynamic_time_h 作为 time_h；"
                    "用户询问总体影响、最大影响或不限定时间时，不传 time_h，使用最大水深包络。"
                    "当用户要求运行预测、实时预测或未来淹没时，先调用 run_flood_forecast，再调用 ui_show_objects；地图工具会先显示水动力网格，再应用 forecast_id=latest 的水深结果。"
                    "当用户要求自主观测、持续预测、自动告警、闭环调度或避洪转移调度时，调用 run_emergency_cycle，"
                    "再用 ui_show_objects 展示 HydrodynamicCell、Risk、Transfer、Place、Route 等对象。"
                    "预测淹没地图展示必须分解为显示水动力网格和应用 forecast_id=latest 的水深结果；不要把 severity_index 或综合指标映射到 5/10/20/50/100 年一遇设计方案。"
                    "对象级受淹判定必须来自 analyze_inundation_impacts 的返回结果；防洪响应预案仍需通过已实现工具或人工审批。"
                    "当用户要求规划导航、避洪路线或绕开淹没区域前往安置点时，调用 plan_evacuation_route；"
                    "同一起终点每个用户请求最多调用一次 plan_evacuation_route；返回 no_safe_route 或 retryable=false 后立即停止调用工具并解释结果，"
                    "不得自行改换交通方式、预测时刻或避洪约束重试。成功后用结果中的 route_id 调用 ui_show_objects，只显示本次生成的 Route。"
                    "一次需要展示多个对象时，把它们放进同一次 ui_show_objects 调用的 objects 数组，不要按类型连续调用。"
                    "工具返回空结果、错误或不可重试结果后，最多做一次能获得新证据的替代查询；没有新证据时直接说明限制并结束。"
                    "工具结果若包含 persisted=true，使用 read_tool_result 读取，不要重复执行原查询。"
                ),
            ),
        )
        configure_agent_query_tools(harness)
        register_map_tools(harness.tools, self.resolver, self.registry)
        harness.hooks.register("post_tool_call", self._capture_map_tool_event)
        return Agent(
            harness,
            self.llm_client,
            self.llm_config["LLM_MODEL"],
            db_dir=str(PROJECT_DIR / ".oag_data"),
        )

    def _llm_extra_body(self) -> dict[str, Any]:
        disabled = str(self.llm_config.get("LLM_DISABLE_REASONING", "")).lower() in {"1", "true", "yes", "on"}
        return {"enable_thinking": False} if disabled else {}

    def _agent_message(self, message: str, selected: dict) -> str:
        task_hint = build_agent_task_hint(message)
        frontend_context = {
            "用户问题": message,
            "选中对象": selected,
            "地图动作工具": ["ui_show_objects", "ui_show_event_marker", "ui_clear_map", "ui_focus_object"],
        }
        return (
            f"用户问题：{message}\n\n"
            f"{task_hint}\n"
            "以下是前端 GIS 的当前状态，只用于帮助你理解用户正在看的地图。"
            "不要把这些前端动作当作领域事实；领域事实必须通过 OAG 工具查询。"
            "如果用户请求地图展示，请调用 ui_* 工具；这些工具只改变前端显示，不改变领域数据。"
            "如果需要对象级受淹判定，调用 analyze_inundation_impacts；不要自行猜测。"
            "当前已有水动力时间轴时，影响问题直接分析当前结果；除非用户明确要求重算，否则不要调用 run_flood_forecast。"
            "如果需要避洪路线规划，调用 plan_evacuation_route，并使用返回的 route_id 显示动态 Route。"
            "同一起终点本轮只规划一次；no_safe_route 或 retryable=false 是终止结果，直接解释，不要改变参数重试。"
            "多个地图对象必须合并到一次 ui_show_objects 调用。工具结果已持久化时调用 read_tool_result，不要重复查询。"
            "如果 hydrodynamic_timeline.mode=time_slice 且用户询问当前时刻/当前画面/该时刻影响，"
            "把 current_hydrodynamic_time_h 作为 analyze_inundation_impacts.time_h；"
            "如果用户询问总体或最大影响，不传 time_h。\n"
            f"{json.dumps(frontend_context, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def agent_session_id(session_id: str) -> str:
        return f"{active_workspace_id() or 'manual'}:{session_id}"

    def _capture_map_tool_event(self, context: dict[str, Any]) -> HookResult:
        tool_name = str(context.get("tool_name") or "")
        session_id = str(context.get("session_id") or "")
        if tool_name == "run_flood_forecast" and session_id:
            result = parse_tool_json_result(context.get("result") or "")
            if result and "error" not in result:
                with self._pending_forecast_results_lock:
                    self._pending_forecast_results.setdefault(session_id, []).append(result)
            return HookResult(action="allow")
        if tool_name == "analyze_inundation_impacts" and session_id:
            result = parse_tool_json_result(context.get("result") or "")
            if result and "error" not in result:
                with self._pending_impact_results_lock:
                    self._pending_impact_results.setdefault(session_id, []).append(result)
            return HookResult(action="allow")
        event = tool_result_to_map_event(
            tool_name,
            str(context.get("result") or ""),
        )
        if not event:
            return HookResult(action="allow")
        if session_id:
            self._queue_pending_map_event(session_id, event)
        return HookResult(action="allow")

    def _queue_pending_map_event(self, session_id: str, event: dict[str, Any]) -> None:
        with self._pending_map_events_lock:
            queue = self._pending_map_events.setdefault(session_id, [])
            signature = json.dumps(event.get("map_actions", []), sort_keys=True, ensure_ascii=False)
            if any(json.dumps(item.get("map_actions", []), sort_keys=True, ensure_ascii=False) == signature for item in queue):
                return
            queue.append(event)

    def _pop_pending_map_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_map_events_lock:
            return self._pending_map_events.pop(session_id, [])

    def _pop_pending_forecast_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_forecast_results_lock:
            return self._pending_forecast_results.pop(session_id, [])

    def _pop_pending_impact_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_impact_results_lock:
            return self._pending_impact_results.pop(session_id, [])


def parse_tool_json_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
