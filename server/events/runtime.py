from __future__ import annotations

import collections
import threading
import time
from typing import Any, TYPE_CHECKING

from domains.flood.runtime.workspace import WORKSPACES, active_workspace_id
from server.events.agent_processor import EventAgentProcessor
from server.events.messages import (
    boundary_flow_observation_detail,
    domain_event_detail,
)
from server.events.playback import (
    BoundaryFlowPlaybackRunner,
    adaptive_playback_speed,
)
from server.serialization import format_sse

if TYPE_CHECKING:
    from server.flood_app import FloodApp


class EventRuntime:
    """Coordinate event playback, queues, child events, and SSE output."""

    def __init__(self, app: FloodApp):
        self.events: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._started = False
        self._playback_running = False
        self._playback_paused = False
        self._event_queue: collections.deque[
            tuple[dict[str, Any], int]
        ] = collections.deque()
        self._event_queue_condition = threading.Condition()
        self._generation = 0
        self._published_inundation_sources: set[str] = set()
        self._published_impact_sources: set[str] = set()
        self._boundary_flow_runner = BoundaryFlowPlaybackRunner()
        self._agent_processor = EventAgentProcessor(
            app=app,
            playback_runner=self._boundary_flow_runner,
            current_generation=lambda: self._generation,
            append_output=self._append_output,
            publish_inundation_event=self._publish_inundation_event_once,
            publish_impact_event=self._publish_impact_event_once,
            side_effects=getattr(app, "side_effects", None),
        )

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
                "detail": (
                    f"后台正以 {self._boundary_flow_runner.speed_multiplier:g}× "
                    "速率按时间顺序回放边界流量观测。"
                ),
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

    def start_playback(self, speed_multiplier: float = 20.0) -> dict[str, Any]:
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

    def restart_playback(
        self,
        speed_multiplier: float = 20.0,
    ) -> dict[str, Any]:
        self.ensure_started()
        self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            if active_workspace_id():
                WORKSPACES.update_manifest(status="stopped")
            WORKSPACES.create()
            self._generation += 1
            self._playback_running = False
            self._playback_paused = False
            self.events.clear()
            self.outputs.clear()
            self._boundary_flow_runner.reset()
            self._published_inundation_sources.clear()
            self._published_impact_sources.clear()
            self._clear_event_queue()
            WORKSPACES.update_manifest(status="ready")
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "reset",
                "label": "演进已重置",
                "detail": (
                    "已创建新的演进工作空间，并回到第一条边界流量观测；"
                    "点击开始演进后继续回放。"
                ),
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return {**self.status(), "status": "reset"}

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
                "detail": (
                    "后台不再回放新的边界流量观测；已清空待处理事件队列。"
                ),
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
                "detail": (
                    "后台已暂停新的边界流量观测；"
                    "已产生的领域事件继续由智能体处理。"
                ),
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            }})
            self.condition.notify_all()
        return {**self.status(), "status": "paused"}

    def resume_playback(
        self,
        speed_multiplier: float = 1.0,
    ) -> dict[str, Any]:
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
                                "detail": (
                                    "点击前端按钮后，后台才会按 CSV 时间过程"
                                    "回放边界流量。"
                                ),
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
            return (
                self._playback_running
                and generation == self._generation
            )

    def _sleep_while_playback_running(
        self,
        seconds: float,
        generation: int,
    ) -> None:
        deadline = time.time() + seconds
        with self.condition:
            while (
                self._playback_running
                and generation == self._generation
            ):
                remaining = deadline - time.time()
                if remaining <= 0:
                    return
                self.condition.wait(timeout=min(remaining, 0.5))

    def _clear_event_queue(self) -> None:
        with self._event_queue_condition:
            self._event_queue.clear()
            self._event_queue_condition.notify_all()

    def _publish_boundary_flow_observation(
        self,
        data: dict[str, Any],
    ) -> None:
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
        self._apply_adaptive_playback_speed(observation)

    def _apply_adaptive_playback_speed(
        self,
        observation: dict[str, Any],
    ) -> None:
        policy_state = self._boundary_flow_runner.playback.policy.state
        target_speed, phase, reason = adaptive_playback_speed(
            observation, policy_state,
        )
        current_speed = self._boundary_flow_runner.speed_multiplier
        if current_speed <= target_speed:
            return
        speed = self._boundary_flow_runner.set_speed(target_speed)
        with self.condition:
            self.outputs.append({"event": "runtime_status", "data": {
                "type": "runtime_status",
                "status": "speed_changed",
                "label": "演进已自动降速",
                "detail": reason,
                "speed_multiplier": speed,
                "speed_phase": phase,
                "automatic": True,
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

    def _finish_playback_sequence(
        self,
        generation: int,
        data: dict[str, Any] | None,
    ) -> None:
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
        event = {
            **event,
            "workspace_id": event.get("workspace_id") or active_workspace_id(),
        }
        generation = self._generation
        with self.condition:
            self.events.append(event)
            self.outputs.append({"event": "domain_event", "data": event})
            self.condition.notify_all()
        self._enqueue_event(event, generation)

    def _enqueue_event(
        self,
        event: dict[str, Any],
        generation: int,
        *,
        priority: bool = False,
    ) -> None:
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
                    self._agent_processor.handle_event(event, generation)
            except Exception as exc:
                self._append_output("agent_trace", {
                    "type": "agent_trace",
                    "tag": "ERR",
                    "label": "事件处理失败",
                    "detail": str(exc),
                    "event_id": event.get("event_id"),
                }, generation)

    def _publish_inundation_event_once(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> None:
        source_id = str(event.get("source_id") or "")
        if not source_id:
            source_id = str(
                (event.get("payload") or {}).get("forecast_id")
                or event.get("event_id")
                or ""
            )
        with self.condition:
            if source_id and source_id in self._published_inundation_sources:
                return
            if source_id:
                self._published_inundation_sources.add(source_id)
        self._publish_child_event(event, generation)

    def _publish_impact_event_once(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> None:
        source_id = str(event.get("source_id") or "")
        if not source_id:
            source_id = str(event.get("event_id") or "")
        with self.condition:
            if source_id and source_id in self._published_impact_sources:
                return
            if source_id:
                self._published_impact_sources.add(source_id)
        self._publish_child_event(event, generation)

    def _publish_child_event(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> None:
        event = {
            **event,
            "workspace_id": event.get("workspace_id") or active_workspace_id(),
        }
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

    def _append_output(
        self,
        event_name: str,
        data: dict[str, Any],
        generation: int | None = None,
    ) -> None:
        with self.condition:
            if generation is not None and generation != self._generation:
                return
            self.outputs.append({
                "event": event_name,
                "data": {
                    **data,
                    "workspace_id": (
                        data.get("workspace_id") or active_workspace_id()
                    ),
                },
            })
            self.condition.notify_all()
