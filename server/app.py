from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DOMAIN_DIR = PROJECT_DIR / "domains" / "flood"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.map_tools import register_map_tools, tool_result_to_map_event  # noqa: E402
from openai import OpenAI  # noqa: E402
from oag.agent import Agent  # noqa: E402
from oag.harness import Harness  # noqa: E402
from oag.ontology.loader import load_domain  # noqa: E402
from oag.runtime import HarnessConfig  # noqa: E402
from oag.runtime.events import event_to_dict  # noqa: E402
from oag.runtime.hooks import HookResult  # noqa: E402
from domains.flood.runtime.hydrodynamic_grid import hydrodynamic_grid_stats, hydrodynamic_grid_tile  # noqa: E402
from domains.flood.runtime.mock_hydrology import HydroMockService  # noqa: E402


ID_FIELDS = {
    "River": "river_id",
    "Watershed": "watershed_id",
    "Waterway": "waterway_id",
    "HydrodynamicBoundary": "boundary_id",
    "County": "county_id",
    "Town": "town_id",
    "Reservoir": "reservoir_id",
    "Sluice": "sluice_id",
    "HydraulicStructure": "structure_id",
    "Road": "road_id",
    "Bridge": "bridge_id",
    "Facility": "facility_id",
    "Place": "place_id",
    "Transfer": "transfer_id",
    "Route": "route_id",
    "Scenario": "scenario_id",
    "Risk": "risk_id",
    "HydroStation": "station_id",
    "HydroObservation": "observation_id",
    "HistoricalFloodMark": "mark_id",
    "Cell": "cell_id",
    "ForecastRun": "forecast_id",
    "ForecastCell": "forecast_cell_id",
    "HydrodynamicCell": "hydrodynamic_cell_id",
}

HYDRO_EVENT_DEBUG_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "ui_show_event_marker",
    "ui_show_objects",
    "run_flood_forecast",
})

INUNDATION_EVENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "ui_show_objects",
})

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
        self.scenarios = self.registry.call("list_scenarios")
        self._pending_map_events: dict[str, list[dict[str, Any]]] = {}
        self._pending_map_events_lock = threading.Lock()
        self._pending_forecast_results: dict[str, list[dict[str, Any]]] = {}
        self._pending_forecast_results_lock = threading.Lock()
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
        mappable = self.registry.call("list_mappable_objects")
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
        }

    def autonomy_cycle(self, force_forecast: bool = False) -> dict:
        return self.registry.call("run_emergency_cycle", force_forecast=force_forecast)

    def forecast(self, force: bool = False) -> dict:
        return self.registry.call("run_flood_forecast", forecast_id="latest", force=force)

    def export_geojson(self, object_type: str, filters: dict,
                       simplify: float = 0) -> tuple[dict, bytes]:
        with self._export_lock:
            result = self.registry.call(
                "export_objects_geojson",
                object_type=object_type,
                filters=filters,
                simplify_tolerance=simplify,
                force=False,
            )
            if "error" in result:
                raise ValueError(result["error"])
            path = Path(result["absolute_path"])
            return result, path.read_bytes()

    def hydrodynamic_grid_stats(self, forecast_id: str = "latest") -> dict[str, Any]:
        return hydrodynamic_grid_stats(forecast_id)

    def hydrodynamic_grid_tile(self, z: int, x: int, y: int,
                               forecast_id: str = "latest",
                               wet_only: bool = False) -> dict[str, Any]:
        return hydrodynamic_grid_tile(z, x, y, forecast_id, wet_only)

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
        try:
            for event in self.agent.chat_stream(agent_message, session_id=run.session_id):
                if run.cancelled:
                    break
                for map_event in self._pop_pending_map_events(run.session_id):
                    self._append_map_event(run, map_event)
                data = event_to_dict(event)
                self._append_event(run, data["type"], data)
            for map_event in self._pop_pending_map_events(run.session_id):
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
                max_turns=6,
                enable_write_confirmation=True,
                llm_extra_body=self._llm_extra_body(),
                runtime_context={
                    "frontend": "GIS-centered flood emergency workspace",
                    "map_rendering": "Frontend renders domain objects by their geometry. Layer is UI state, not a domain object.",
                },
                append_system_prompt=(
                    "你服务于一个以珊瑚河流域 GIS 为中心的前端。"
                    "回答用户时优先调用领域对象查询和领域函数获取事实。"
                    "不要让用户通过情景切换控件操作；如果需要其他情景，请让用户直接在对话中指定。"
                    "当用户要求在地图上显示、打开、绘制、加载、叠加、缩放、聚焦或清空对象时，"
                    "必须调用 ui_show_objects、ui_show_event_marker、ui_focus_object 或 ui_clear_map，让前端执行地图动作；"
                    "当用户要求清除、不显示或隐藏淹没范围、预测淹没结果、水动力结果时，调用 ui_clear_map 并传 target=inundation；这只移除淹没结果，不改变地图视野。"
                    "不要只用文字说明将要显示什么。"
                    "当前 analyze_risks/plan_response/generate_brief 可能尚未实现；如工具返回 not_implemented，必须如实说明。"
                    "当用户要求运行预测、实时预测或未来淹没时，先调用 run_flood_forecast，再调用 ui_show_objects；地图工具会先显示水动力网格，再应用 forecast_id=latest 的水深结果。"
                    "当用户要求自主观测、持续预测、自动告警、闭环调度或避洪转移调度时，调用 run_emergency_cycle，"
                    "再用 ui_show_objects 展示 HydrodynamicCell、Risk、Transfer、Place、Route 等对象。"
                    "预测淹没地图展示必须分解为显示水动力网格和应用 forecast_id=latest 的水深结果；不要把 severity_index 或综合指标映射到 5/10/20/50/100 年一遇情景。"
                    "在空间叠加风险函数实现前，不得声称已经判定某个学校、道路、桥梁或路线受淹；只能说明已展示对象和淹没范围，可作为研判基础。"
                ),
            ),
        )
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
        frontend_context = {
            "用户问题": message,
            "选中对象": selected,
            "地图动作工具": ["ui_show_objects", "ui_show_event_marker", "ui_clear_map", "ui_focus_object"],
        }
        return (
            f"用户问题：{message}\n\n"
            "以下是前端 GIS 的当前状态，只用于帮助你理解用户正在看的地图。"
            "不要把这些前端动作当作领域事实；领域事实必须通过 OAG 工具查询。"
            "如果用户请求地图展示，请调用 ui_* 工具；这些工具只改变前端显示，不改变领域数据。"
            "如果 analyze_risks 返回 not_implemented，不要声称已经完成对象级受淹判定。\n"
            f"{json.dumps(frontend_context, ensure_ascii=False, indent=2)}"
        )

    def _capture_map_tool_event(self, context: dict[str, Any]) -> HookResult:
        tool_name = str(context.get("tool_name") or "")
        session_id = str(context.get("session_id") or "")
        if tool_name == "run_flood_forecast" and session_id:
            result = parse_tool_json_result(context.get("result") or "")
            if result and "error" not in result:
                with self._pending_forecast_results_lock:
                    self._pending_forecast_results.setdefault(session_id, []).append(result)
            return HookResult(action="allow")
        event = tool_result_to_map_event(
            tool_name,
            str(context.get("result") or ""),
        )
        if not event:
            return HookResult(action="allow")
        if session_id:
            with self._pending_map_events_lock:
                self._pending_map_events.setdefault(session_id, []).append(event)
        return HookResult(action="allow")

    def _pop_pending_map_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_map_events_lock:
            return self._pending_map_events.pop(session_id, [])

    def _pop_pending_forecast_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_forecast_results_lock:
            return self._pending_forecast_results.pop(session_id, [])


APP = FloodApp()


class AgentRun:
    def __init__(self, run_id: str, session_id: str, message: str,
                 selected: dict | None = None):
        self.run_id = run_id
        self.session_id = session_id
        self.message = message
        self.selected = selected or {}
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.cancelled = False
        self.seq = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.condition = threading.Condition()


class AgentRunManager:
    def __init__(self, app: FloodApp):
        self.app = app
        self._runs: dict[str, AgentRun] = {}
        self._active_by_session: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, session_id: str, message: str,
              selected: dict | None = None) -> AgentRun:
        run = AgentRun(uuid.uuid4().hex, session_id, message, selected)
        with self._lock:
            self._runs[run.run_id] = run
            self._active_by_session[session_id] = run.run_id
        thread = threading.Thread(target=self._execute, args=(run,), daemon=True)
        thread.start()
        return run

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active(self, session_id: str) -> AgentRun | None:
        with self._lock:
            run_id = self._active_by_session.get(session_id)
            run = self._runs.get(run_id) if run_id else None
        if not run:
            return None
        with run.condition:
            return None if run.done or run.cancelled else run

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if not run:
            return False
        with run.condition:
            run.cancelled = True
            run.condition.notify_all()
        return True

    def stream(self, run: AgentRun, since: int = 0):
        yield self._format_sse("run", {
            "type": "run",
            "run_id": run.run_id,
            "session_id": run.session_id,
            "done": run.done,
            "seq": run.seq,
        })

        next_seq = max(1, int(since or 0) + 1)
        while True:
            pending = []
            done = False
            should_ping = False
            with run.condition:
                while not run.done and not run.cancelled and run.seq < next_seq:
                    run.condition.wait(timeout=15)
                    if run.seq < next_seq:
                        should_ping = True
                        break
                pending = [event for event in run.events if int(event.get("seq", 0)) >= next_seq]
                done = run.done or run.cancelled
            if should_ping:
                yield self._format_sse("ping", {"type": "ping"})
                continue
            for event in pending:
                next_seq = int(event["seq"]) + 1
                yield self._format_sse(event["type"], event["data"])
            if done and not pending:
                break

    def active_info(self, session_id: str) -> dict:
        run = self.get_active(session_id)
        if not run:
            return {"run_id": None}
        with run.condition:
            return {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "seq": run.seq,
                "done": run.done,
                "cancelled": run.cancelled,
            }

    def _execute(self, run: AgentRun):
        try:
            self.app.stream_chat(run)
        finally:
            self.app._append_event(run, "done", {"type": "done"})
            with run.condition:
                run.done = True
                run.updated_at = time.time()
                run.condition.notify_all()
            with self._lock:
                if self._active_by_session.get(run.session_id) == run.run_id:
                    self._active_by_session.pop(run.session_id, None)

    @staticmethod
    def _format_sse(event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


RUNS = AgentRunManager(APP)


class EventRuntime:
    def __init__(self, app: FloodApp):
        self.app = app
        self.stop_after_inundation_event = True
        self.events: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._started = False
        self._event_queue: collections.deque[tuple[dict[str, Any], int]] = collections.deque()
        self._event_queue_condition = threading.Condition()
        self._generation = 0
        self._published_inundation_sources: set[str] = set()
        self._stations = {
            row["station_id"]: row
            for row in self.app.resolver.query("HydroStation")
        }
        self._hydro_mock = HydroMockService(self._stations)

    def reset(self) -> None:
        with self.condition:
            self._generation += 1
            self.events.clear()
            self.outputs.clear()
            self._hydro_mock.reset()
            self._published_inundation_sources.clear()
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "label": "事件驱动闭环已启动",
                "detail": "后台水文站 mock 服务正在推送观测；超阈值事件会携带四边界流量过程线并进入单 worker 队列。",
            }})
            self.outputs.append({"event": "map_actions", "data": {
                "type": "map_actions",
                "context": "调试 · 水文事件到智能体",
                "map_actions": [
                    {"type": "reset"},
                ],
                "result_cards": [
                    {"title": "调试范围", "value": "水文事件", "detail": "水文异常事件由 LLM 决定地图展示和是否启动水动力预测。"},
                ],
            }})
            self.condition.notify_all()

    def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        self.reset()
        threading.Thread(target=self._hydro_station_loop, daemon=True).start()
        threading.Thread(target=self._event_worker_loop, daemon=True).start()

    def stream(self, interval: int):
        self.ensure_started()
        with self.condition:
            next_seq = max(0, len(self.outputs) - 80)
        while True:
            pending = []
            with self.condition:
                while len(self.outputs) <= next_seq:
                    self.condition.wait(timeout=max(1, interval))
                    if len(self.outputs) <= next_seq:
                        yield Handler._format_sse("runtime_status", {
                            "type": "runtime_status",
                            "label": "等待水文事件",
                            "detail": "后台服务继续观测水文站 mock 数据。",
                        })
                pending = self.outputs[next_seq:]
                next_seq = len(self.outputs)
            for item in pending:
                yield Handler._format_sse(item["event"], item["data"])

    def _hydro_station_loop(self) -> None:
        time.sleep(1.0)
        while True:
            observation = self._hydro_mock.next_observation()
            self._publish_observation_status(observation)
            event = self._hydro_mock.threshold_event(observation)
            if event:
                self._publish_event(event)
            time.sleep(5)

    def _publish_observation_status(self, observation: dict[str, Any]) -> None:
        label = (
            f"{observation.get('station_name')}{observation.get('metric_label')}"
            f" {observation.get('value')} {observation.get('unit')}"
        )
        detail = "正常观测" if observation.get("status") == "normal" else "超过阈值，准备发布领域事件"
        with self.condition:
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "label": label,
                "detail": detail,
            }})
            self.condition.notify_all()

    def _publish_event(self, event: dict[str, Any]) -> None:
        generation = self._generation
        with self.condition:
            self.events.append(event)
            self.outputs.append({"event": "domain_event", "data": event})
            self.outputs.append({"event": "agent_trace", "data": {
                "type": "agent_trace",
                "tag": "EVENT",
                "label": event["title"],
                "detail": hydro_event_detail(event),
                "event_id": event["event_id"],
            }})
            boundary_flow = ((event.get("payload") or {}).get("boundary_flow") or {})
            if boundary_flow:
                self.outputs.append({"event": "agent_trace", "data": {
                    "type": "agent_trace",
                    "tag": "DATA",
                    "label": "四边界流量已生成",
                    "detail": boundary_flow_detail(boundary_flow),
                    "event_id": event["event_id"],
                }})
            self.condition.notify_all()
        self._enqueue_event(event, generation)

    def _enqueue_event(self, event: dict[str, Any], generation: int, *, priority: bool = False) -> None:
        with self._event_queue_condition:
            if priority:
                self._event_queue.appendleft((event, generation))
            else:
                self._event_queue.append((event, generation))
            self._event_queue_condition.notify()

    def _event_worker_loop(self) -> None:
        while True:
            with self._event_queue_condition:
                while not self._event_queue:
                    self._event_queue_condition.wait()
                event, generation = self._event_queue.popleft()
            try:
                if generation == self._generation:
                    self._handle_event(event, generation)
            except Exception as exc:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "ERR",
                    "label": "事件处理失败",
                    "detail": str(exc),
                    "event_id": event.get("event_id"),
                }, generation)

    def _handle_event(self, event: dict[str, Any], generation: int) -> None:
        if generation != self._generation:
            return
        if event.get("event_type") == "HydroThresholdExceeded":
            agent_result = self._run_agent_for_hydro_event(event, generation)
            trace = self._reason_about_hydro_event(event)
            self._append_output("agent_trace", trace, generation)
            forecast_result = agent_result.get("forecast_result")
            if not forecast_result and agent_result.get("forecast_requested"):
                forecast_result = self.app.forecast(force=False)
            if forecast_result and not forecast_was_skipped(forecast_result):
                inundation_event = self._make_inundation_event(event, forecast_result, trace.get("severity", "warning"))
                self._publish_inundation_event_once(inundation_event, generation)
            return
        if event.get("event_type") == "InundationGenerated":
            self._run_agent_for_inundation_event(event, generation)
            return

    def _run_agent_for_hydro_event(self, event: dict[str, Any], generation: int) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        prompt = (
            "/no_think\n"
            "你正在作为珊瑚河洪水应急智能体接收后台事件。"
            "本轮链路调试放开到水动力预测：你需要判断该水文异常事件的意义。"
            "如果你认为该事件需要用户在地图上感知，必须调用 ui_show_event_marker 展示事件 marker，"
            "并设置 show_source=true、fit=true。"
            "事件 payload 中包含 boundary_flow 和 forecast_trigger。"
            "只有当 forecast_trigger.should_run_forecast 为 true，且你判断确需推演时，才调用 run_flood_forecast，forecast_id 使用 latest。"
            "禁止调用 run_emergency_cycle；影响评估和防洪响应预案仍然切断。"
            "请用简短结论说明：事件来源、指标值、阈值、是否调用了预测模型。"
            "原始事件如下：\n"
            f"{json.dumps(event, ensure_ascii=False, indent=2)}"
        )
        agent_result: dict[str, Any] = {}
        reasoning_chunks: list[str] = []
        text_chunks: list[str] = []
        try:
            for raw_event in self.app.agent.chat_stream(
                prompt,
                session_id=session_id,
                allowed_tools=HYDRO_EVENT_DEBUG_TOOLS,
            ):
                if generation != self._generation:
                    return agent_result
                self._collect_agent_side_effects(session_id, agent_result, generation)
                self._publish_forecast_result_from_agent(event, agent_result, generation)
                data = event_to_dict(raw_event)
                event_type = data.get("type")
                if event_type == "tool_call":
                    tool_name = data.get("name", "")
                    if tool_name == "run_flood_forecast":
                        agent_result["forecast_requested"] = True
                    self._append_output("agent_trace", {
                        "type": "agent_trace",
                        "tag": "CALL",
                        "label": readable_event_tool(tool_name),
                        "detail": json.dumps(data.get("args") or {}, ensure_ascii=False),
                    }, generation)
                elif event_type == "tool_result":
                    self._append_output("agent_trace", {
                        "type": "agent_trace",
                        "tag": "RESULT",
                        "label": readable_event_tool(data.get("name", "")),
                        "detail": compact_event_text(data.get("result") or ""),
                    }, generation)
                    if data.get("name") == "run_flood_forecast":
                        agent_result["forecast_requested"] = True
                        parsed = parse_tool_json_result(data.get("result") or "")
                        if parsed and "error" not in parsed:
                            agent_result["forecast_result"] = parsed
                        elif not data.get("blocked"):
                            agent_result["forecast_result"] = self.app.forecast(force=False)
                        self._publish_forecast_result_from_agent(event, agent_result, generation)
                elif event_type == "reasoning":
                    reasoning_chunks.append(str(data.get("content") or ""))
                elif event_type == "text":
                    text_chunks.append(str(data.get("content") or ""))
            self._collect_agent_side_effects(session_id, agent_result, generation)
            if agent_result.get("forecast_requested") and not agent_result.get("forecast_result"):
                agent_result["forecast_result"] = self.app.forecast(force=False)
            self._publish_forecast_result_from_agent(event, agent_result, generation)
            reasoning = "".join(reasoning_chunks).strip()
            if reasoning:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "THINK",
                    "label": "LLM 事件推理",
                    "detail": compact_event_text(reasoning),
                }, generation)
            conclusion = "".join(text_chunks).strip()
            if conclusion:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "TEXT",
                    "label": "智能体结论",
                    "detail": compact_event_text(conclusion),
                }, generation)
        except Exception as exc:
            self._append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "FALLBACK",
                "label": "LLM 事件推理失败，启用规则兜底",
                "detail": str(exc),
            }, generation)
            return agent_result
        return agent_result

    def _run_agent_for_inundation_event(self, event: dict[str, Any], generation: int) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        prompt = (
            "/no_think\n"
            "你正在作为珊瑚河洪水应急智能体接收水动力模型输出事件。"
            "本轮链路调试只放开到地图展示预测淹没范围。"
            "如果你认为该淹没事件需要在 GIS 上展示，必须调用 ui_show_objects 显示预测淹没结果，"
            "对象使用 HydrodynamicCell，filters 使用 {\"forecast_id\":\"latest\"}，fit=false，refresh=true；地图工具会拆成显示网格和应用水深结果。"
            "禁止调用 run_emergency_cycle、analyze_risks 或 plan_response；影响评估和防洪响应预案仍然切断。"
            "请用简短结论说明：预测运行、淹没面积、最大水深，以及是否已请求地图展示。"
            "原始事件如下：\n"
            f"{json.dumps(event, ensure_ascii=False, indent=2)}"
        )
        return self._run_agent_for_followup_event(
            prompt,
            session_id,
            generation,
            allowed_tools=INUNDATION_EVENT_TOOLS,
            fallback_label="LLM 淹没事件推理失败",
        )

    def _publish_forecast_result_from_agent(self, source_event: dict[str, Any],
                                            agent_result: dict[str, Any],
                                            generation: int) -> None:
        forecast_result = agent_result.get("forecast_result")
        if not forecast_result or agent_result.get("forecast_event_published") or forecast_was_skipped(forecast_result):
            return
        trace = self._reason_about_hydro_event(source_event)
        inundation_event = self._make_inundation_event(
            source_event,
            forecast_result,
            trace.get("severity", "warning"),
        )
        self._publish_inundation_event_once(inundation_event, generation)
        agent_result["forecast_event_published"] = True

    def _run_agent_for_followup_event(self, prompt: str, session_id: str, generation: int,
                                      allowed_tools: frozenset[str],
                                      fallback_label: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        reasoning_chunks: list[str] = []
        text_chunks: list[str] = []
        try:
            for raw_event in self.app.agent.chat_stream(
                prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
            ):
                if generation != self._generation:
                    return result
                self._collect_agent_side_effects(session_id, result, generation)
                data = event_to_dict(raw_event)
                event_type = data.get("type")
                if event_type == "tool_call":
                    self._append_output("agent_trace", {
                        "type": "agent_trace",
                        "tag": "CALL",
                        "label": readable_event_tool(data.get("name", "")),
                        "detail": json.dumps(data.get("args") or {}, ensure_ascii=False),
                    }, generation)
                elif event_type == "tool_result":
                    self._append_output("agent_trace", {
                        "type": "agent_trace",
                        "tag": "RESULT",
                        "label": readable_event_tool(data.get("name", "")),
                        "detail": compact_event_text(data.get("result") or ""),
                    }, generation)
                elif event_type == "reasoning":
                    reasoning_chunks.append(str(data.get("content") or ""))
                elif event_type == "text":
                    text_chunks.append(str(data.get("content") or ""))
            self._collect_agent_side_effects(session_id, result, generation)
            reasoning = "".join(reasoning_chunks).strip()
            if reasoning:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "THINK",
                    "label": "LLM 事件推理",
                    "detail": compact_event_text(reasoning),
                }, generation)
            conclusion = "".join(text_chunks).strip()
            if conclusion:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "TEXT",
                    "label": "智能体结论",
                    "detail": compact_event_text(conclusion),
                }, generation)
        except Exception as exc:
            self._append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "FALLBACK",
                "label": fallback_label,
                "detail": str(exc),
            }, generation)
        return result

    def _collect_agent_side_effects(self, session_id: str, result: dict[str, Any], generation: int) -> None:
        for forecast_result in self.app._pop_pending_forecast_results(session_id):
            result["forecast_result"] = forecast_result
        for map_event in self.app._pop_pending_map_events(session_id):
            self._append_output("map_actions", map_event, generation)

    def _make_inundation_event(self, source_event: dict[str, Any],
                               forecast_result: dict[str, Any],
                               severity: str) -> dict[str, Any]:
        forecast = forecast_result.get("forecast") or {}
        return {
            "type": "domain_event",
            "event_id": f"evt_{uuid.uuid4().hex[:10]}",
            "event_type": "InundationGenerated",
            "source_type": "HydrodynamicModel",
            "source_id": forecast.get("forecast_id", "latest"),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "severity": severity,
            "title": "水动力模型生成预测淹没范围",
            "payload": forecast,
            "correlation_id": source_event["correlation_id"],
        }

    def _publish_inundation_event_once(self, event: dict[str, Any], generation: int) -> None:
        source_id = str(event.get("source_id") or "")
        if not source_id:
            source_id = str((event.get("payload") or {}).get("forecast_id") or event.get("event_id") or "")
        with self.condition:
            if source_id and source_id in self._published_inundation_sources:
                return
            if source_id:
                self._published_inundation_sources.add(source_id)
        self._publish_child_event(event, generation)

    def _publish_child_event(self, event: dict[str, Any], generation: int) -> None:
        with self.condition:
            if generation != self._generation:
                return
            self.events.append(event)
            self.outputs.append({"event": "domain_event", "data": event})
            self.outputs.append({"event": "agent_trace", "data": {
                "type": "agent_trace",
                "tag": "EVENT",
                "label": event["title"],
                "detail": inundation_event_detail(event),
                "event_id": event["event_id"],
            }})
            self.condition.notify_all()
        self._enqueue_event(event, generation, priority=True)

    def _reason_about_hydro_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        should_run = bool(trigger.get("should_run_forecast"))
        if self.app.agent:
            detail = "智能体接收水文异常事件；LLM 可调用地图工具，并依据四边界流量判断是否调用 run_flood_forecast。"
        else:
            detail = "未启用 LLM，按四边界流量规则决定是否启动水动力模型。"
        return {
            "type": "agent_trace",
            "tag": "SYSTEM",
            "label": "领域规则提示",
            "detail": (
                f"{detail} {payload.get('metric_label')}={payload.get('value')} {payload.get('unit')}，"
                f"阈值={payload.get('threshold')} {payload.get('unit')}；"
                f"规则建议={trigger.get('decision', 'unknown')}，原因={trigger.get('reason', '')}"
            ),
            "should_run_model": should_run,
            "severity": event.get("severity", "warning"),
        }

    def _append_output(self, event_name: str, data: dict[str, Any], generation: int | None = None) -> None:
        with self.condition:
            if generation is not None and generation != self._generation:
                return
            self.outputs.append({"event": event_name, "data": data})
            self.condition.notify_all()

def readable_event_tool(name: str) -> str:
    return {
        "run_flood_forecast": "运行水动力模型",
        "run_emergency_cycle": "运行预警调度闭环",
        "ui_show_objects": "地图展示对象",
        "ui_show_event_marker": "地图展示事件",
        "ui_focus_object": "地图聚焦对象",
        "ui_clear_map": "清空地图",
    }.get(name, name or "tool")


def compact_event_text(value: Any, limit: int = 360) -> str:
    text = str(value or "")
    return f"{text[:limit]}..." if len(text) > limit else text


def parse_tool_json_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def hydro_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return (
        f"{payload.get('station_name')}: {payload.get('metric_label')} "
        f"{payload.get('value')} {payload.get('unit')} > 阈值 "
        f"{payload.get('threshold')} {payload.get('unit')}"
    )


def boundary_flow_detail(summary: dict[str, Any]) -> str:
    boundaries = summary.get("boundaries") or {}
    parts = []
    for key in ("interval1", "interval2", "tonggu", "upstream"):
        item = boundaries.get(key) or {}
        if item:
            parts.append(f"{item.get('label', key)}峰值 {format_float(item.get('peak_flow_m3s'), 2)} m³/s")
    return (
        f"{summary.get('boundary_flow_id')}: "
        f"模板={summary.get('template_scenario_id')}，"
        f"flow_index={format_float(summary.get('flow_index'), 2)}；"
        + "，".join(parts)
    )


def forecast_was_skipped(result: dict[str, Any]) -> bool:
    forecast = result.get("forecast") if isinstance(result, dict) else None
    return isinstance(forecast, dict) and str(forecast.get("status") or "").startswith("skipped")


def inundation_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    area = format_float(payload.get("inundated_area_km2"), 2)
    depth = format_float(payload.get("max_depth_m"), 2)
    return (
        f"{payload.get('name') or payload.get('forecast_id')}: "
        f"预测单元 {payload.get('forecast_cell_count', 0)} 个，"
        f"淹没面积 {area} km²，最大水深 {depth} m"
    )


def format_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"


def forecast_map_event(result: dict[str, Any]) -> dict[str, Any]:
    forecast = result.get("forecast") or {}
    return {
        "type": "map_actions",
        "context": "事件驱动 · 水动力计算",
        "map_actions": [
            {
                "type": "show_hydrodynamic_mesh",
                "fit": False,
            },
            {
                "type": "apply_hydrodynamic_result",
                "label": "预测淹没范围",
                "filters": {"forecast_id": "latest"},
                "fit": False,
                "refresh": True,
            },
        ],
        "result_cards": [
            {
                "title": "模型输出",
                "value": f"{forecast.get('inundated_area_km2', 0):.2f} km²",
                "detail": f"预测单元 {forecast.get('forecast_cell_count', 0)} 个，最大水深 {forecast.get('max_depth_m', 0):.2f} m。",
            },
        ],
    }


def decision_map_event(result: dict[str, Any]) -> dict[str, Any]:
    transfer_ids = [row.get("transfer_id") for row in result.get("transfer_impacts", []) if row.get("transfer_id")]
    road_ids = [row.get("object_id") for row in result.get("road_impacts", []) if row.get("object_id")]
    route_ids = [row.get("object_id") for row in result.get("route_impacts", []) if row.get("object_id")]
    warning = result.get("warning") or {}
    return {
        "type": "map_actions",
        "context": "事件驱动 · 预案研判",
        "map_actions": [
            {"type": "load_object", "object_type": "Transfer", "label": "转移对象", "filters": {}, "fit": False},
            {"type": "load_object", "object_type": "Place", "label": "安置地点", "filters": {}, "fit": False},
            {"type": "load_object", "object_type": "Route", "label": "转移路线", "filters": {}, "fit": False},
            {"type": "highlight_objects", "object_type": "Transfer", "object_ids": transfer_ids[:12], "fit": True},
            {"type": "highlight_objects", "object_type": "Road", "object_ids": road_ids[:8], "fit": False},
            {"type": "highlight_objects", "object_type": "Route", "object_ids": route_ids[:8], "fit": False},
        ],
        "result_cards": [
            {
                "title": warning.get("title", "预警决策"),
                "value": str(warning.get("level", "")).upper(),
                "detail": warning.get("basis", ""),
            },
            {
                "title": "调度建议",
                "value": str(len(result.get("recommendations") or [])),
                "detail": f"转移影响 {len(result.get('transfer_impacts') or [])} 个，道路关注 {len(result.get('road_impacts') or [])} 个。",
            },
        ],
    }


EVENT_RUNTIME = EventRuntime(APP)


class Handler(BaseHTTPRequestHandler):
    server_version = "FloodFrontend/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                return self._json(APP.bootstrap())
            if parsed.path == "/api/agent/chat/stream":
                return self._chat_stream(parsed.query)
            if parsed.path == "/api/autonomy/stream":
                return self._autonomy_stream(parsed.query)
            if parsed.path == "/api/agent/runs/active":
                return self._active_run(parsed.query)
            if parsed.path == "/api/hydrodynamic-grid/meta":
                return self._hydrodynamic_grid_meta(parsed.query)
            if parsed.path == "/api/hydrodynamic-grid/tile":
                return self._hydrodynamic_grid_tile(parsed.query)
            if parsed.path == "/api/geojson":
                return self._geojson(parsed.query)
            if parsed.path == "/api/object":
                return self._object(parsed.query)
            return self._static(parsed.path)
        except Exception as exc:
            return self._json({"error": str(exc)}, status=500)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            target = STATIC_DIR / "index.html"
            if target.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(target.stat().st_size))
                self.end_headers()
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/agent/confirm":
                return self._confirm(payload)
            if parsed.path == "/api/autonomy/reset":
                EVENT_RUNTIME.reset()
                EVENT_RUNTIME.ensure_started()
                return self._json({"ok": True})
            if parsed.path.startswith("/api/agent/runs/") and parsed.path.endswith("/cancel"):
                run_id = parsed.path.split("/")[-2]
                return self._json({"ok": RUNS.cancel(run_id), "run_id": run_id})
            return self._json({"error": "not found"}, status=404)
        except Exception as exc:
            return self._json({"error": str(exc)}, status=500)

    def _chat_stream(self, query: str):
        params = parse_qs(query)
        run_id = (params.get("run_id") or [""])[0]
        session_id = (params.get("session_id") or ["frontend-default"])[0]
        since = int((params.get("since") or ["0"])[0] or 0)

        if run_id:
            run = RUNS.get(run_id)
            if not run:
                return self._sse([
                    AgentRunManager._format_sse("text", {
                        "type": "text",
                        "content": "上一次生成任务已经不存在，请重新提问。",
                    }),
                    AgentRunManager._format_sse("done", {"type": "done"}),
                ])
            return self._sse(RUNS.stream(run, since))

        message = unquote((params.get("message") or [""])[0])
        selected_raw = (params.get("selected") or ["{}"])[0]
        try:
            selected = json.loads(unquote(selected_raw))
        except Exception:
            selected = {}
        if not message:
            return self._json({"error": "message is required"}, status=400)
        run = RUNS.start(session_id, message, selected)
        return self._sse(RUNS.stream(run, since=0))

    def _active_run(self, query: str):
        params = parse_qs(query)
        session_id = (params.get("session_id") or ["frontend-default"])[0]
        return self._json(RUNS.active_info(session_id))

    def _confirm(self, payload: dict):
        session_id = str(payload.get("session_id") or "frontend-default")
        approved = bool(payload.get("approved"))
        answer = payload.get("answer")
        if not APP.agent or not APP.agent.has_pending(session_id):
            return self._json({"error": "no pending confirmation"}, status=400)

        def generator():
            try:
                for event in APP.agent.confirm_tool(session_id, approved, answer=answer):
                    data = event_to_dict(event)
                    yield AgentRunManager._format_sse(data["type"], data)
            except Exception as exc:
                yield AgentRunManager._format_sse("text", {
                    "type": "text",
                    "content": f"确认后继续执行失败：{exc}",
                })
            yield AgentRunManager._format_sse("done", {"type": "done"})

        return self._sse(generator())

    def _autonomy_stream(self, query: str):
        params = parse_qs(query)
        interval = max(5, int((params.get("interval") or ["5"])[0] or 5))
        return self._sse(EVENT_RUNTIME.stream(interval))

    def _geojson(self, query: str):
        params = parse_qs(query)
        object_type = (params.get("object_type") or [""])[0]
        if not object_type:
            return self._json({"error": "object_type is required"}, status=400)
        simplify = float((params.get("simplify_tolerance") or ["0"])[0] or 0)
        raw_filters = (params.get("filters") or ["{}"])[0]
        try:
            filters = json.loads(raw_filters) if raw_filters else {}
        except json.JSONDecodeError:
            return self._json({"error": "filters must be a JSON object"}, status=400)
        if not isinstance(filters, dict):
            return self._json({"error": "filters must be a JSON object"}, status=400)
        filters.update({
            key: _coerce_query_value(values[0])
            for key, values in params.items()
            if key not in {"object_type", "simplify_tolerance", "filters"} and values
        })
        _, body = APP.export_geojson(object_type, filters, simplify)
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _hydrodynamic_grid_tile(self, query: str):
        params = parse_qs(query)
        try:
            z = int((params.get("z") or [""])[0])
            x = int((params.get("x") or [""])[0])
            y = int((params.get("y") or [""])[0])
        except (TypeError, ValueError):
            return self._json({"error": "z, x and y are required integers"}, status=400)
        forecast_id = self._hydrodynamic_result_id(params)
        wet_only = str((params.get("wet_only") or [""])[0]).lower() in {"1", "true", "yes", "on"}
        data = APP.hydrodynamic_grid_tile(z, x, y, forecast_id, wet_only)
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _hydrodynamic_grid_meta(self, query: str):
        params = parse_qs(query)
        forecast_id = self._hydrodynamic_result_id(params)
        return self._json(APP.hydrodynamic_grid_stats(forecast_id))

    def _hydrodynamic_result_id(self, params: dict[str, list[str]]) -> str:
        result = (params.get("result") or [""])[0]
        if result:
            return result
        scenario_id = (params.get("scenario_id") or [""])[0]
        if scenario_id:
            return scenario_id
        return_period = (params.get("return_period_year") or [""])[0]
        if return_period:
            try:
                period = int(return_period)
            except ValueError:
                period = 0
            scenario = next((row for row in APP.scenarios if row.get("return_period_year") == period), None)
            if scenario:
                return str(scenario.get("scenario_id") or "latest")
        return (params.get("forecast_id") or ["latest"])[0]

    def _object(self, query: str):
        params = parse_qs(query)
        object_type = (params.get("object_type") or [""])[0]
        object_id = (params.get("id") or [""])[0]
        if not object_type or not object_id:
            return self._json({"error": "object_type and id are required"}, status=400)
        return self._json(APP.get_object(object_type, object_id))

    def _static(self, path: str):
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for chunk in chunks:
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except BrokenPipeError:
                break

    @staticmethod
    def _format_sse(event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def log_message(self, fmt: str, *args):
        print(f"{self.address_string()} - {fmt % args}")


def _coerce_query_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Flood server running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
