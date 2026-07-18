from __future__ import annotations

import collections
import json
import threading
import time
import uuid
from typing import Any, Callable

from oag.runtime.events import event_to_dict

from domains.flood.runtime.mock_boundary_flow import BoundaryFlowMockService


BOUNDARY_FLOW_EVENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "ui_show_objects",
    "run_flood_forecast",
})

INUNDATION_EVENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "ui_show_objects",
    "analyze_inundation_impacts",
})


def format_sse(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


class BoundaryFlowMockRunner:
    def __init__(self, source: BoundaryFlowMockService | None = None,
                 interval_seconds: float = 5.0):
        self.source = source or BoundaryFlowMockService()
        self.interval_seconds = interval_seconds

    def reset(self) -> None:
        self.source.reset()

    def run_forever(self, *,
                    wait_until_running: Callable[[], int],
                    is_running: Callable[[int], bool],
                    publish_data: Callable[[dict[str, Any]], None],
                    should_promote: Callable[[dict[str, Any]], bool],
                    publish_event: Callable[[dict[str, Any]], None],
                    finish_sequence: Callable[[int, dict[str, Any]], None],
                    sleep_while_running: Callable[[float, int], None]) -> None:
        while True:
            generation = wait_until_running()
            time.sleep(1.0)
            if not is_running(generation):
                continue
            data = self.source.next_boundary_flow_data()
            if not is_running(generation):
                continue
            publish_data(data)
            if should_promote(data):
                publish_event(data)
                finish_sequence(generation, data)
                continue
            sleep_while_running(self.interval_seconds, generation)


class EventRuntime:
    def __init__(self, app: FloodApp):
        self.app = app
        self.stop_after_inundation_event = True
        self.events: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._started = False
        self._mock_running = False
        self._event_queue: collections.deque[tuple[dict[str, Any], int]] = collections.deque()
        self._event_queue_condition = threading.Condition()
        self._generation = 0
        self._published_inundation_sources: set[str] = set()
        self._published_impact_sources: set[str] = set()
        self._boundary_flow_runner = BoundaryFlowMockRunner()

    def reset(self) -> None:
        with self.condition:
            self._generation += 1
            self.events.clear()
            self.outputs.clear()
            self._boundary_flow_runner.reset()
            self._published_inundation_sources.clear()
            self._published_impact_sources.clear()
            self._clear_event_queue()
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "running",
                "label": "边界流量 mock 服务已启动",
                "detail": "后台边界流量 mock 服务正在生成四边界流量数据。",
            }})
            self.condition.notify_all()

    def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(
            target=self._boundary_flow_runner.run_forever,
            kwargs={
                "wait_until_running": self._wait_until_mock_running,
                "is_running": self._is_mock_running,
                "publish_data": self._publish_boundary_flow_data,
                "should_promote": self._should_promote_boundary_flow_data,
                "publish_event": self._publish_event,
                "finish_sequence": self._finish_mock_sequence,
                "sleep_while_running": self._sleep_while_mock_running,
            },
            daemon=True,
        ).start()
        threading.Thread(target=self._event_worker_loop, daemon=True).start()

    def start_mock(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if self._mock_running:
                return self.status()
            self._mock_running = True
        self.reset()
        return self.status()

    def stop_mock(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if not self._mock_running:
                return self.status()
            self._mock_running = False
            self._generation += 1
            self._clear_event_queue()
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "stopped",
                "label": "边界流量 mock 服务已停止",
                "detail": "后台不再生成新的边界流量事件；已清空待处理事件队列。",
            }})
            self.condition.notify_all()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self.condition:
            return {
                "running": self._mock_running,
                "started": self._started,
                "event_count": len(self.events),
                "output_count": len(self.outputs),
            }

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
                        yield format_sse("runtime_status", {
                            "type": "runtime_status",
                            "label": "等待启动 mock 服务",
                            "detail": "点击前端按钮后，后台才会生成边界流量 mock 数据。",
                        })
                pending = self.outputs[next_seq:]
                next_seq = len(self.outputs)
            for item in pending:
                yield format_sse(item["event"], item["data"])

    def _wait_until_mock_running(self) -> int:
        with self.condition:
            while not self._mock_running:
                self.condition.wait()
            return self._generation

    def _is_mock_running(self, generation: int) -> bool:
        with self.condition:
            return self._mock_running and generation == self._generation

    def _sleep_while_mock_running(self, seconds: float, generation: int) -> None:
        deadline = time.time() + seconds
        with self.condition:
            while self._mock_running and generation == self._generation:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                self.condition.wait(timeout=min(remaining, 0.5))

    def _clear_event_queue(self) -> None:
        with self._event_queue_condition:
            self._event_queue.clear()
            self._event_queue_condition.notify_all()

    def _publish_boundary_flow_data(self, data: dict[str, Any]) -> None:
        payload = data.get("payload") or {}
        boundary_flow = payload.get("boundary_flow") or {}
        with self.condition:
            self.outputs.append({"event": "boundary_flow_data", "data": {
                "type": "boundary_flow_data",
                "label": "四边界流量数据",
                "event": data,
                "detail": boundary_flow_detail(boundary_flow),
            }})
            self.condition.notify_all()

    def _should_promote_boundary_flow_data(self, data: dict[str, Any]) -> bool:
        payload = data.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        return bool(trigger.get("should_run_forecast"))

    def _finish_mock_sequence(self, generation: int, data: dict[str, Any]) -> None:
        payload = data.get("payload") or {}
        boundary_flow = payload.get("boundary_flow") or {}
        with self.condition:
            if generation != self._generation:
                return
            self._mock_running = False
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "finished",
                "label": "边界流量 mock 服务已结束",
                "detail": boundary_flow_detail(boundary_flow),
            }})
            self.condition.notify_all()

    def _publish_event(self, event: dict[str, Any]) -> None:
        generation = self._generation
        with self.condition:
            self.events.append(event)
            self.outputs.append({"event": "domain_event", "data": event})
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
        if event.get("event_type") == "BoundaryFlowSeriesGenerated":
            agent_result = self._run_agent_for_boundary_flow_event(event, generation)
            trace = self._reason_about_boundary_flow_event(event)
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

    def _run_agent_for_boundary_flow_event(self, event: dict[str, Any], generation: int) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        prompt = (
            "/no_think\n"
            "你正在作为珊瑚河洪水应急智能体接收后台事件。"
            "本轮链路调试放开到水动力预测：你需要判断这组四边界流量数据是否需要驱动水动力模型。"
            "如果你认为该事件需要用户在地图上感知，可以调用 ui_show_objects 展示 HydrodynamicBoundary，filters 使用 {\"is_model_input_boundary\": true}，fit=false。"
            "事件 payload 中包含 boundary_flow 和 forecast_trigger。"
            "只有当 forecast_trigger.should_run_forecast 为 true，且你判断确需推演时，才调用 run_flood_forecast，forecast_id 使用 latest。"
            "禁止调用 run_emergency_cycle；影响评估和防洪响应预案仍然切断。"
            "请用简短结论说明四边界流量峰值，以及是否调用了预测模型。"
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
                allowed_tools=BOUNDARY_FLOW_EVENT_TOOLS,
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
                    "detail": compact_event_text(conclusion, limit=1800),
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
            "本轮链路放开到预测淹没范围展示和受影响对象分析。"
            "请按领域工具自身的语义说明调用可用函数完成对象影响分析，不要自行猜测受淹对象。"
            "如果你认为该淹没事件需要在 GIS 上展示，必须调用 ui_show_objects 显示预测淹没结果，"
            "对象使用 HydrodynamicCell，filters 使用 {\"forecast_id\":\"latest\"}，fit=false，refresh=true；地图工具会拆成显示网格和应用水深结果。"
            "禁止调用 run_emergency_cycle、analyze_risks 或 plan_response；防洪响应预案仍然切断。"
            "请用简短结论说明：预测运行、淹没面积、最大水深、影响分析结果，以及是否已请求地图展示。"
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
        trace = self._reason_about_boundary_flow_event(source_event)
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
                    tool_name = str(data.get("name") or "")
                    parsed = parse_tool_json_result(data.get("result") or "")
                    if tool_name == "analyze_inundation_impacts" and is_impact_result(parsed):
                        result["impact_result"] = parsed
                        self._publish_impact_event_once(
                            self._make_impact_event(parsed, session_id),
                            generation,
                        )
                    self._append_output("agent_trace", {
                        "type": "agent_trace",
                        "tag": "RESULT",
                        "label": readable_event_tool(tool_name),
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
                    "detail": compact_event_text(conclusion, limit=1800),
                }, generation)
            self._append_followup_complete_trace(result, generation)
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
        for impact_result in self.app._pop_pending_impact_results(session_id):
            result["impact_result"] = impact_result
            self._publish_impact_event_once(
                self._make_impact_event(impact_result, session_id),
                generation,
            )
        for map_event in self.app._pop_pending_map_events(session_id):
            self._append_output("map_actions", map_event, generation)

    def _append_followup_complete_trace(self, result: dict[str, Any], generation: int) -> None:
        impact_result = result.get("impact_result")
        if is_impact_result(impact_result):
            detail = impact_event_detail({"payload": impact_result})
        else:
            detail = "事件智能体处理已结束。"
        self._append_output("agent_trace", {
            "type": "agent_trace",
            "tag": "DONE",
            "label": "事件处理完成",
            "detail": detail,
        }, generation)

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

    def _make_impact_event(self, impact_result: dict[str, Any],
                           session_id: str) -> dict[str, Any]:
        return {
            "type": "domain_event",
            "event_id": f"evt_{uuid.uuid4().hex[:10]}",
            "event_type": "ImpactAnalyzed",
            "source_type": "OntologyFunction",
            "source_id": str(impact_result.get("forecast_id") or "latest"),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "severity": impact_event_severity(impact_result),
            "title": "预测淹没影响对象分析完成",
            "payload": impact_result,
            "correlation_id": session_id,
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

    def _publish_impact_event_once(self, event: dict[str, Any], generation: int) -> None:
        source_id = str(event.get("source_id") or "")
        if not source_id:
            source_id = str(event.get("event_id") or "")
        with self.condition:
            if source_id and source_id in self._published_impact_sources:
                return
            if source_id:
                self._published_impact_sources.add(source_id)
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
                "detail": domain_event_detail(event),
                "event_id": event["event_id"],
            }})
            self.condition.notify_all()
        self._enqueue_event(event, generation, priority=True)

    def _reason_about_boundary_flow_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        boundary_flow = payload.get("boundary_flow") or {}
        should_run = bool(trigger.get("should_run_forecast"))
        if self.app.agent:
            detail = "智能体接收四边界流量数据。"
        else:
            detail = "未启用 LLM，已接收四边界流量数据。"
        return {
            "type": "agent_trace",
            "tag": "SYSTEM",
            "label": "四边界流量数据",
            "detail": f"{detail} {boundary_flow_detail(boundary_flow)}",
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
        "analyze_inundation_impacts": "分析淹没影响对象",
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


def is_impact_result(value: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(value, dict)
        and "error" not in value
        and "summary" in value
        and "impacts" in value
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
        + "，".join(parts)
    )


def boundary_flow_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return boundary_flow_detail(payload.get("boundary_flow") or {})


def forecast_was_skipped(result: dict[str, Any]) -> bool:
    forecast = result.get("forecast") if isinstance(result, dict) else None
    return isinstance(forecast, dict) and str(forecast.get("status") or "").startswith("skipped")


def domain_event_detail(event: dict[str, Any]) -> str:
    if event.get("event_type") == "InundationGenerated":
        return inundation_event_detail(event)
    if event.get("event_type") == "ImpactAnalyzed":
        return impact_event_detail(event)
    return str(event.get("severity") or "")


def inundation_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    area = format_float(payload.get("inundated_area_km2"), 2)
    depth = format_float(payload.get("max_depth_m"), 2)
    return (
        f"{payload.get('name') or payload.get('forecast_id')}: "
        f"预测单元 {payload.get('forecast_cell_count', 0)} 个，"
        f"淹没面积 {area} km²，最大水深 {depth} m"
    )


def impact_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    summary = payload.get("summary") or {}
    labels = {
        "Facility": "设施",
        "Bridge": "桥梁",
        "Road": "道路",
        "Route": "路线",
        "Transfer": "转移单元",
        "Place": "安置点",
    }
    parts = []
    for key in ("Facility", "Bridge", "Road", "Route", "Transfer", "Place"):
        item = summary.get(key) or {}
        count = int(item.get("count") or 0)
        if count:
            parts.append(f"{labels[key]} {count} 个")
    return "，".join(parts) if parts else "未识别到受预测淹没影响的对象"


def impact_event_severity(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    critical = sum(int((item or {}).get("critical") or 0) for item in summary.values())
    high = sum(int((item or {}).get("high") or 0) for item in summary.values())
    if critical:
        return "critical"
    if high:
        return "warning"
    if int(result.get("total_impacts") or 0):
        return "info"
    return "normal"


def format_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"
