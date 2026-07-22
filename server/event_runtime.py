from __future__ import annotations

import collections
import json
import threading
import time
import uuid
from typing import Any, Callable

from oag.runtime.events import event_to_dict

from domains.flood.runtime.boundary_flow import BoundaryFlowPlayback
from domains.flood.runtime.workspace import WORKSPACES, active_workspace_id, workspace_scope


BOUNDARY_FLOW_EVENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "run_flood_forecast",
})

INUNDATION_EVENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "ui_show_objects",
})


def format_sse(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


class BoundaryFlowPlaybackRunner:
    def __init__(self, playback: BoundaryFlowPlayback | None = None,
                 interval_seconds: float = 5.0):
        self.playback = playback or BoundaryFlowPlayback()
        self.base_interval_seconds = interval_seconds
        self._speed_multiplier = 1.0
        self._speed_lock = threading.Lock()

    @property
    def interval_seconds(self) -> float:
        with self._speed_lock:
            return self.base_interval_seconds / self._speed_multiplier

    @property
    def speed_multiplier(self) -> float:
        with self._speed_lock:
            return self._speed_multiplier

    def set_speed(self, multiplier: float) -> float:
        value = float(multiplier)
        if value not in {1.0, 2.0, 5.0, 10.0}:
            raise ValueError("playback speed must be one of 1, 2, 5, 10")
        with self._speed_lock:
            self._speed_multiplier = value
        return value

    def reset(self) -> None:
        self.playback.reset()

    def mark_forecast_started(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_started(forecast_input_id)

    def mark_forecast_completed(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_completed(forecast_input_id)

    def mark_forecast_failed(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_failed(forecast_input_id)

    def status(self) -> dict[str, Any]:
        return {
            **self.playback.status(),
            "speed_multiplier": self.speed_multiplier,
            "interval_seconds": self.interval_seconds,
        }

    def run_forever(self, *,
                    wait_until_running: Callable[[], int],
                    is_running: Callable[[int], bool],
                    publish_observation: Callable[[dict[str, Any]], None],
                    publish_policy_event: Callable[[dict[str, Any]], None],
                    finish_sequence: Callable[[int, dict[str, Any] | None], None],
                    sleep_while_running: Callable[[float, int], None]) -> None:
        while True:
            generation = wait_until_running()
            time.sleep(1.0)
            if not is_running(generation):
                continue
            self.play_generation(
                generation=generation,
                is_running=is_running,
                publish_observation=publish_observation,
                publish_policy_event=publish_policy_event,
                finish_sequence=finish_sequence,
                sleep_while_running=sleep_while_running,
            )

    def play_generation(self, *, generation: int,
                        is_running: Callable[[int], bool],
                        publish_observation: Callable[[dict[str, Any]], None],
                        publish_policy_event: Callable[[dict[str, Any]], None],
                        finish_sequence: Callable[[int, dict[str, Any] | None], None],
                        sleep_while_running: Callable[[float, int], None]) -> None:
        last_observation: dict[str, Any] | None = None
        while is_running(generation):
            observation_event, policy_events = self.playback.next_events()
            if observation_event is None:
                finish_sequence(generation, last_observation)
                return
            if not is_running(generation):
                return
            last_observation = observation_event
            publish_observation(observation_event)
            for event in policy_events:
                publish_policy_event(event)
            sleep_while_running(self.interval_seconds, generation)


class EventRuntime:
    def __init__(self, app: FloodApp):
        self.app = app
        self.stop_after_inundation_event = True
        self.events: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._started = False
        self._playback_running = False
        self._playback_paused = False
        self._event_queue: collections.deque[tuple[dict[str, Any], int]] = collections.deque()
        self._event_queue_condition = threading.Condition()
        self._generation = 0
        self._published_inundation_sources: set[str] = set()
        self._published_impact_sources: set[str] = set()
        self._boundary_flow_runner = BoundaryFlowPlaybackRunner()

    def reset(self) -> None:
        with self.condition:
            self._generation += 1
            self._playback_paused = False
            self.events.clear()
            self.outputs.clear()
            self._boundary_flow_runner.reset()
            self._published_inundation_sources.clear()
            self._published_impact_sources.clear()
            self._clear_event_queue()
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "running",
                "label": "边界流量过程回放已启动",
                "detail": f"后台正以 {self._boundary_flow_runner.speed_multiplier:g}× 速率按时间顺序回放边界流量观测。",
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()

    def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(
            target=self._boundary_flow_runner.run_forever,
            kwargs={
                "wait_until_running": self._wait_until_playback_running,
                "is_running": self._is_playback_running,
                "publish_observation": self._publish_boundary_flow_observation,
                "publish_policy_event": self._publish_policy_event,
                "finish_sequence": self._finish_playback_sequence,
                "sleep_while_running": self._sleep_while_playback_running,
            },
            daemon=True,
        ).start()
        threading.Thread(target=self._event_worker_loop, daemon=True).start()

    def start_playback(self, speed_multiplier: float = 1.0) -> dict[str, Any]:
        self.ensure_started()
        self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            if self._playback_running:
                return self.status()
        WORKSPACES.create()
        with self.condition:
            self._playback_running = True
        self.reset()
        return self.status()

    def set_playback_speed(self, speed_multiplier: float) -> dict[str, Any]:
        self.ensure_started()
        speed = self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "speed_changed",
                "label": "演进速率已调整",
                "detail": f"边界流量过程回放速率调整为 {speed:g}×。",
                "speed_multiplier": speed,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return self.status()

    def stop_playback(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if not self._playback_running and not self._playback_paused:
                return self.status()
            self._playback_running = False
            self._playback_paused = False
            self._generation += 1
            self._clear_event_queue()
            WORKSPACES.update_manifest(status="stopped")
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "stopped",
                "label": "边界流量过程回放已停止",
                "detail": "后台不再回放新的边界流量观测；已清空待处理事件队列。",
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return self.status()

    def pause_playback(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if not self._playback_running:
                return self.status()
            self._playback_running = False
            self._playback_paused = True
            WORKSPACES.update_manifest(status="paused")
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "paused",
                "label": "边界流量过程回放已暂停",
                "detail": "后台已暂停新的边界流量观测；已产生的领域事件继续由智能体处理。",
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return {**self.status(), "status": "paused"}

    def resume_playback(self, speed_multiplier: float = 1.0) -> dict[str, Any]:
        self.ensure_started()
        self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            if self._playback_running:
                return self.status()
            if not self._playback_paused:
                raise ValueError("boundary flow playback is not paused")
            self._playback_running = True
            self._playback_paused = False
            WORKSPACES.update_manifest(status="active")
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "running",
                "label": "边界流量过程回放已继续",
                "detail": "后台从暂停位置继续回放边界流量观测。",
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return {**self.status(), "status": "running"}

    def status(self) -> dict[str, Any]:
        with self.condition:
            return {
                "running": self._playback_running,
                "paused": self._playback_paused,
                "started": self._started,
                "event_count": len(self.events),
                "output_count": len(self.outputs),
                "workspace_id": active_workspace_id(),
                **self._boundary_flow_runner.status(),
            }

    def stream(self, interval: int):
        self.ensure_started()
        with self.condition:
            next_seq = max(0, len(self.outputs) - 80)
        while True:
            pending: list[dict[str, Any]] = []
            heartbeat: dict[str, Any] | None = None
            with self.condition:
                if len(self.outputs) < next_seq:
                    next_seq = 0
                if len(self.outputs) <= next_seq:
                    self.condition.wait(timeout=max(1, interval))
                    if len(self.outputs) < next_seq:
                        next_seq = 0
                    if len(self.outputs) <= next_seq:
                        if not self._playback_running:
                            heartbeat = {
                                "type": "runtime_status",
                                "label": "等待启动边界流量回放",
                                "detail": "点击前端按钮后，后台才会按 CSV 时间过程回放边界流量。",
                            }
                    else:
                        pending = self.outputs[next_seq:]
                        next_seq = len(self.outputs)
                else:
                    pending = self.outputs[next_seq:]
                    next_seq = len(self.outputs)
            if heartbeat:
                yield format_sse("runtime_status", heartbeat)
            for item in pending:
                yield format_sse(item["event"], item["data"])

    def _wait_until_playback_running(self) -> int:
        with self.condition:
            while not self._playback_running:
                self.condition.wait()
            return self._generation

    def _is_playback_running(self, generation: int) -> bool:
        with self.condition:
            return self._playback_running and generation == self._generation

    def _sleep_while_playback_running(self, seconds: float, generation: int) -> None:
        deadline = time.time() + seconds
        with self.condition:
            while self._playback_running and generation == self._generation:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                self.condition.wait(timeout=min(remaining, 0.5))

    def _clear_event_queue(self) -> None:
        with self._event_queue_condition:
            self._event_queue.clear()
            self._event_queue_condition.notify_all()

    def _publish_boundary_flow_observation(self, data: dict[str, Any]) -> None:
        data = {**data, "workspace_id": active_workspace_id()}
        observation = (data.get("payload") or {}).get("observation") or {}
        with self.condition:
            self.outputs.append({"event": "boundary_flow_data", "data": {
                "type": "boundary_flow_data",
                "label": "四边界流量观测",
                "event": data,
                "detail": boundary_flow_observation_detail(observation),
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()

    def _publish_policy_event(self, event: dict[str, Any]) -> None:
        event = {**event, "workspace_id": active_workspace_id()}
        if event.get("event_type") == "FloodForecastRequired":
            self._publish_event(event)
            return
        with self.condition:
            self.events.append(event)
            self.outputs.append({"event": "domain_event", "data": event})
            self.condition.notify_all()

    def _finish_playback_sequence(self, generation: int,
                                  data: dict[str, Any] | None) -> None:
        observation = ((data or {}).get("payload") or {}).get("observation") or {}
        with self.condition:
            if generation != self._generation:
                return
            self._playback_running = False
            self._playback_paused = False
            WORKSPACES.update_manifest(status="finished")
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "finished",
                "label": "边界流量过程回放已结束",
                "detail": boundary_flow_observation_detail(observation),
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()

    def _publish_event(self, event: dict[str, Any]) -> None:
        event = {**event, "workspace_id": event.get("workspace_id") or active_workspace_id()}
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
        workspace_id = str(event.get("workspace_id") or active_workspace_id() or "")
        with workspace_scope(workspace_id or None):
            if generation != self._generation:
                return
            if event.get("event_type") == "FloodForecastRequired":
                agent_result = self._run_agent_for_forecast_required_event(event, generation)
                trace = self._reason_about_forecast_required_event(event)
                self._append_output("agent_trace", trace, generation)
                forecast_result = agent_result.get("forecast_result")
                if not forecast_result and agent_result.get("forecast_requested"):
                    forecast_result = self.app.forecast(force=False)
                if forecast_result:
                    self._record_forecast_policy_result(event, forecast_result, agent_result)
                if forecast_completed(forecast_result):
                    inundation_event = self._make_inundation_event(event, forecast_result, trace.get("severity", "warning"))
                    self._publish_inundation_event_once(inundation_event, generation)
                return
            if event.get("event_type") == "InundationGenerated":
                self._run_agent_for_inundation_event(event, generation)
                return

    def _run_agent_for_forecast_required_event(self, event: dict[str, Any], generation: int) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        prompt = (
            "/no_think\n"
            "你正在作为珊瑚河洪水应急智能体接收后台事件。"
            "领域策略已根据连续边界流量观测生成 FloodForecastRequired 事件。"
            "你需要结合事件中的当前观测、触发原因和模型输入摘要，判断是否调用水动力模型。"
            "事件 payload 中包含 observation、forecast_input 和 forecast_trigger。"
            "如果确需推演，调用 run_flood_forecast，forecast_id 使用 latest；该函数会读取事件对应的稳定输入快照。"
            "禁止调用 run_emergency_cycle；影响评估和防洪响应预案仍然切断。"
            "请用简短结论说明当前四边界流量、触发原因，以及是否调用了预测模型。"
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
                        self._boundary_flow_runner.mark_forecast_started(str(event.get("source_id") or ""))
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
            "本轮链路只放开到预测淹没结果研判和地图展示。"
            "如果你认为该淹没事件需要在 GIS 上展示，必须调用 ui_show_objects 显示预测淹没结果，"
            "对象使用 HydrodynamicCell，filters 使用 {\"forecast_id\":\"latest\"}，fit=false，refresh=true；地图工具会拆成显示网格和应用水深结果。"
            "受影响对象由前端根据水动力时间轴自动计算，并以独立轻量 Marker 展示；"
            "不要分析、猜测或通过 ui_show_objects 加载和高亮 Facility、Bridge、Transfer、Place、Road、Route。"
            "禁止调用 run_emergency_cycle；防洪响应预案仍然切断。"
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
            map_event_filter=filter_inundation_map_event,
        )

    def _publish_forecast_result_from_agent(self, source_event: dict[str, Any],
                                            agent_result: dict[str, Any],
                                            generation: int) -> None:
        forecast_result = agent_result.get("forecast_result")
        if not forecast_result or agent_result.get("forecast_event_published"):
            return
        self._record_forecast_policy_result(source_event, forecast_result, agent_result)
        if not forecast_completed(forecast_result):
            return
        trace = self._reason_about_forecast_required_event(source_event)
        inundation_event = self._make_inundation_event(
            source_event,
            forecast_result,
            trace.get("severity", "warning"),
        )
        self._publish_inundation_event_once(inundation_event, generation)
        agent_result["forecast_event_published"] = True

    def _record_forecast_policy_result(self, source_event: dict[str, Any],
                                       forecast_result: dict[str, Any],
                                       agent_result: dict[str, Any]) -> None:
        if agent_result.get("forecast_policy_recorded"):
            return
        forecast = forecast_result.get("forecast") or {}
        status = str(forecast.get("status") or "")
        forecast_input_id = str(event_forecast_input_id(source_event) or "")
        if status == "completed":
            self._boundary_flow_runner.mark_forecast_completed(forecast_input_id)
            agent_result["forecast_policy_recorded"] = True
        elif status == "failed":
            self._boundary_flow_runner.mark_forecast_failed(forecast_input_id)
            agent_result["forecast_policy_recorded"] = True

    def _run_agent_for_followup_event(self, prompt: str, session_id: str, generation: int,
                                      allowed_tools: frozenset[str],
                                      fallback_label: str,
                                      map_event_filter: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
                                      ) -> dict[str, Any]:
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
                self._collect_agent_side_effects(
                    session_id,
                    result,
                    generation,
                    map_event_filter=map_event_filter,
                )
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
            self._collect_agent_side_effects(
                session_id,
                result,
                generation,
                map_event_filter=map_event_filter,
            )
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

    def _collect_agent_side_effects(self, session_id: str, result: dict[str, Any], generation: int,
                                    map_event_filter: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
                                    ) -> None:
        for forecast_result in self.app._pop_pending_forecast_results(session_id):
            result["forecast_result"] = forecast_result
        for impact_result in self.app._pop_pending_impact_results(session_id):
            result["impact_result"] = impact_result
            self._publish_impact_event_once(
                self._make_impact_event(impact_result, session_id),
                generation,
            )
        for map_event in self.app._pop_pending_map_events(session_id):
            filtered_event = map_event_filter(map_event) if map_event_filter else map_event
            if filtered_event:
                self._append_output("map_actions", filtered_event, generation)

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
            "source_id": (
                f"{forecast.get('forecast_id', 'latest')}:"
                f"{forecast.get('forecast_input_id') or event_forecast_input_id(source_event) or forecast.get('generated_at', '')}"
            ),
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
        event = {**event, "workspace_id": event.get("workspace_id") or active_workspace_id()}
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

    def _reason_about_forecast_required_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        observation = payload.get("observation") or {}
        should_run = bool(trigger.get("should_run_forecast"))
        if self.app.agent:
            detail = "智能体接收洪水预测请求。"
        else:
            detail = "未启用 LLM，洪水预测请求保持待处理。"
        return {
            "type": "agent_trace",
            "tag": "SYSTEM",
            "label": "洪水预测请求",
            "detail": f"{detail} {boundary_flow_observation_detail(observation)} {trigger.get('reason', '')}",
            "should_run_model": should_run,
            "severity": event.get("severity", "warning"),
        }

    def _append_output(self, event_name: str, data: dict[str, Any], generation: int | None = None) -> None:
        with self.condition:
            if generation is not None and generation != self._generation:
                return
            self.outputs.append({
                "event": event_name,
                "data": {**data, "workspace_id": data.get("workspace_id") or active_workspace_id()},
            })
            self.condition.notify_all()


def filter_inundation_map_event(event: dict[str, Any]) -> dict[str, Any] | None:
    allowed_types = {"show_hydrodynamic_mesh", "apply_hydrodynamic_result"}
    actions = [
        action for action in event.get("map_actions") or []
        if isinstance(action, dict) and action.get("type") in allowed_types
    ]
    if not actions:
        return None
    return {
        **event,
        "map_actions": actions,
        "result_cards": [],
    }


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


def boundary_flow_observation_detail(observation: dict[str, Any]) -> str:
    boundaries = observation.get("boundaries") or {}
    parts = []
    for key in ("interval1", "interval2", "tonggu", "upstream"):
        item = boundaries.get(key) or {}
        if item:
            parts.append(f"{item.get('label', key)} {format_float(item.get('flow_m3s'), 2)} m³/s")
    return (
        f"{observation.get('observed_at', '')}: "
        + "，".join(parts)
    )


def boundary_flow_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return boundary_flow_observation_detail(payload.get("observation") or {})


def event_forecast_input_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    forecast_input = payload.get("forecast_input") or {}
    return str(forecast_input.get("boundary_flow_id") or event.get("source_id") or "")


def forecast_completed(result: dict[str, Any] | None) -> bool:
    forecast = result.get("forecast") if isinstance(result, dict) else None
    return isinstance(forecast, dict) and str(forecast.get("status") or "") == "completed"


def domain_event_detail(event: dict[str, Any]) -> str:
    if event.get("event_type") == "FloodForecastRequired":
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        return f"{boundary_flow_event_detail(event)}；{trigger.get('reason', '')}"
    if event.get("event_type") == "FloodEpisodeEnded":
        payload = event.get("payload") or {}
        return f"{payload.get('ended_at', '')}，共生成 {payload.get('forecast_versions', 0)} 个预测输入版本"
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
