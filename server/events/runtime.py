from __future__ import annotations

import collections
import json
import threading
import time
from typing import Any, TYPE_CHECKING

from domains.flood.runtime.boundary_flow import (
    BoundaryFlowPlayback,
    BoundaryFlowPlaybackSource,
)
from domains.flood.runtime.playback_sources import PlaybackSourceRegistry
from domains.flood.runtime.workspace import WORKSPACES, active_workspace_id
from server.events.agent_processor import EventAgentProcessor
from server.events.factory import make_directive_issued_event
from server.events.messages import (
    boundary_flow_forecast_detail,
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

    def __init__(
        self,
        app: FloodApp,
        playback_sources: PlaybackSourceRegistry | None = None,
    ):
        self.events: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.condition = threading.Condition()
        self._started = False
        self._playback_running = False
        self._playback_paused = False
        self._playback_processing = False
        self._auto_pause_enabled = True
        self._playback_phase = "ready"
        self._processing_generation: int | None = None
        self._processing_event_id = ""
        self._processing_correlation_id = ""
        self._processing_followup_pending = False
        self._event_queue: collections.deque[
            tuple[dict[str, Any], int]
        ] = collections.deque()
        self._event_queue_condition = threading.Condition()
        self._generation = 0
        self._published_inundation_sources: set[str] = set()
        self._published_impact_sources: set[str] = set()
        self._playback_sources = playback_sources or PlaybackSourceRegistry()
        initial_source = self._playback_sources.get(
            self._playback_sources.selected_source_id
        )
        self._current_playback_source = initial_source.public(selected=True)
        self._prepared_workspace_id: str | None = None
        self._boundary_flow_runner = BoundaryFlowPlaybackRunner(
            BoundaryFlowPlayback(BoundaryFlowPlaybackSource(initial_source.csv_path))
        )
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
            self._clear_processing_locked()
            self._playback_phase = "running"
            self.events.clear()
            self.outputs.clear()
            self._boundary_flow_runner.reset()
            self._published_inundation_sources.clear()
            self._published_impact_sources.clear()
            self._clear_event_queue()
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "running",
                "label": "边界流量过程回放已启动",
                "detail": (
                    f"后台正以 {self._boundary_flow_runner.speed_multiplier:g}× "
                    "速率按时间顺序回放边界流量预测时刻。"
                ),
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            })
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

    def start_playback(
        self,
        speed_multiplier: float = 20.0,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_started()
        self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            if self._playback_running and not source_id:
                return self.status()
            if source_id:
                self._playback_sources.get(source_id)
                self._playback_running = False
                self._playback_paused = False
                self._clear_processing_locked()
                self._generation += 1
                self._clear_event_queue()
                if active_workspace_id():
                    WORKSPACES.update_manifest(status="stopped")
        manifest = WORKSPACES.active_manifest()
        force_new_workspace = bool(source_id)
        if force_new_workspace or not manifest or manifest.get("status") != "ready":
            if active_workspace_id() and not force_new_workspace:
                WORKSPACES.update_manifest(status="stopped")
            WORKSPACES.create()
            self._prepare_playback_source(
                source_id or self._playback_sources.selected_source_id
            )
        elif self._prepared_workspace_id != active_workspace_id():
            self._restore_workspace_playback_source(manifest)
        with self.condition:
            self._playback_running = True
        self.reset()
        return self.status()

    def restart_playback(
        self,
        speed_multiplier: float = 20.0,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_started()
        self._boundary_flow_runner.set_speed(speed_multiplier)
        if source_id:
            self._playback_sources.get(source_id)
        with self.condition:
            if active_workspace_id():
                WORKSPACES.update_manifest(status="stopped")
            WORKSPACES.create()
            self._prepare_playback_source(
                source_id or self._playback_sources.selected_source_id
            )
            self._generation += 1
            self._playback_running = False
            self._playback_paused = False
            self._clear_processing_locked()
            self._playback_phase = "ready"
            self.events.clear()
            self.outputs.clear()
            self._boundary_flow_runner.reset()
            self._published_inundation_sources.clear()
            self._published_impact_sources.clear()
            self._clear_event_queue()
            WORKSPACES.update_manifest(status="ready")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "reset",
                "label": "演进已重置",
                "detail": (
                    "已创建新的演进工作空间，并回到第一个边界流量预测时刻；"
                    "点击开始演进后继续回放。"
                ),
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return {**self.status(), "status": "reset"}

    def set_playback_speed(self, speed_multiplier: float) -> dict[str, Any]:
        self.ensure_started()
        speed = self._boundary_flow_runner.set_speed(speed_multiplier)
        with self.condition:
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "speed_changed",
                "label": "演进速率已调整",
                "detail": f"边界流量过程回放速率调整为 {speed:g}×。",
                "speed_multiplier": speed,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return self.status()

    def set_auto_pause(self, enabled: bool) -> dict[str, Any]:
        self.ensure_started()
        if not isinstance(enabled, bool):
            raise ValueError("auto_pause_enabled must be a boolean")
        with self.condition:
            self._auto_pause_enabled = enabled
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "auto_pause_changed",
                "label": "自动暂停已开启" if enabled else "自动暂停已关闭",
                "detail": (
                    "当前时刻事件链处理完成后，演进将自动进入暂停状态。"
                    if enabled
                    else
                    "当前时刻事件链处理完成后，演进将自动继续播放下一时刻。"
                ),
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return {**self.status(), "status": "auto_pause_changed"}

    def stop_playback(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if (
                not self._playback_running
                and not self._playback_paused
                and not self._playback_processing
            ):
                return self.status()
            self._playback_running = False
            self._playback_paused = False
            self._clear_processing_locked()
            self._playback_phase = "stopped"
            self._generation += 1
            self._clear_event_queue()
            WORKSPACES.update_manifest(status="stopped")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "stopped",
                "label": "边界流量过程回放已停止",
                "detail": (
                    "后台不再回放新的边界流量预测时刻；已清空待处理事件队列。"
                ),
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return self.status()

    def pause_playback(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if self._playback_processing:
                return self.status()
            if not self._playback_running:
                return self.status()
            self._playback_running = False
            self._playback_paused = True
            self._playback_phase = "paused"
            WORKSPACES.update_manifest(status="paused")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "paused",
                "label": "边界流量过程回放已暂停",
                "detail": (
                    "后台已暂停新的边界流量预测时刻；"
                    "已产生的领域事件继续由智能体处理。"
                ),
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            })
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
            self._clear_processing_locked()
            self._playback_phase = "running"
            WORKSPACES.update_manifest(status="active")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "running",
                "label": "边界流量过程回放已继续",
                "detail": "后台从暂停位置继续回放边界流量预测时刻。",
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return {**self.status(), "status": "running"}

    def step_playback(self) -> dict[str, Any]:
        self.ensure_started()
        with self.condition:
            if self._playback_running or not self._playback_paused:
                raise ValueError("演进当前不在暂停状态")
            policy = self._boundary_flow_runner.playback.policy
            if policy.state == policy.PENDING:
                raise ValueError("CNN 洪水预测尚未完成")
            generation = self._generation
            observation_event, policy_events = self._boundary_flow_runner.step()
            if observation_event is None:
                self._finish_playback_sequence(generation, None)
                return {**self.status(), "status": "finished", "stepped": False}

            self._publish_boundary_flow_observation(observation_event)
            for event in policy_events:
                self._publish_policy_event(event)
            observation = (
                (observation_event.get("payload") or {}).get("observation") or {}
            )
            forecast_triggered = bool(policy_events)
            if forecast_triggered:
                return {
                    **self.status(),
                    "status": "processing",
                    "stepped": True,
                    "forecast_triggered": True,
                }
            WORKSPACES.update_manifest(status="paused")
            playback_status = self._boundary_flow_runner.status()
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "stepped",
                "label": "演进已单步推进",
                "detail": (
                    f"已推进到 {observation.get('simulation_time') or observation.get('observed_at') or ''}；"
                    + (
                        "新窗口仍有超限点，已触发滚动洪水预测。"
                        if forecast_triggered
                        else "新窗口未触发洪水预测。"
                    )
                ),
                "forecast_triggered": forecast_triggered,
                "running": False,
                "paused": True,
                **playback_status,
                "step_available": (
                    playback_status.get("policy_state") != "PENDING"
                    and bool(playback_status.get("has_next"))
                ),
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
        return {
            **self.status(),
            "status": "stepped",
            "stepped": True,
            "forecast_triggered": forecast_triggered,
        }

    def status(self) -> dict[str, Any]:
        with self.condition:
            playback_status = self._boundary_flow_runner.status()
            return {
                "running": self._playback_running,
                "paused": self._playback_paused,
                "processing": self._playback_processing,
                "auto_pause_enabled": self._auto_pause_enabled,
                "playback_phase": self._playback_phase,
                "started": self._started,
                "event_count": len(self.events),
                "output_count": len(self.outputs),
                "workspace_id": active_workspace_id(),
                "playback_source": dict(self._current_playback_source),
                **playback_status,
                "step_available": (
                    self._playback_paused
                    and playback_status.get("policy_state") != "PENDING"
                    and bool(playback_status.get("has_next"))
                ),
            }

    def list_playback_sources(self) -> dict[str, Any]:
        return self._playback_sources.list_sources()

    def upload_playback_source(
        self,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        source = self._playback_sources.upload(filename, content)
        return {"source": source.public(selected=False)}

    def _prepare_playback_source(self, source_id: str) -> None:
        workspace_id = active_workspace_id()
        if not workspace_id:
            raise RuntimeError("无法为演进数据创建工作空间")
        csv_path, metadata = self._playback_sources.snapshot(
            source_id,
            WORKSPACES.path(workspace_id, create=True),
        )
        self._boundary_flow_runner.replace_source(csv_path)
        self._current_playback_source = metadata
        self._prepared_workspace_id = workspace_id
        WORKSPACES.update_manifest(
            playback_source=metadata,
            playback_input="inputs/boundary_flow.csv",
        )

    def _restore_workspace_playback_source(self, manifest: dict[str, Any]) -> None:
        workspace_id = active_workspace_id()
        if not workspace_id:
            return
        csv_path = WORKSPACES.path(workspace_id) / "inputs" / "boundary_flow.csv"
        metadata = manifest.get("playback_source")
        if not csv_path.is_file() or not isinstance(metadata, dict):
            self._prepare_playback_source(self._playback_sources.selected_source_id)
            return
        self._boundary_flow_runner.replace_source(csv_path)
        self._current_playback_source = dict(metadata)
        self._prepared_workspace_id = workspace_id
        source_id = str(metadata.get("source_id") or "")
        if source_id:
            self._playback_sources.select(source_id)

    def publish_directive_issued(self, directive: dict[str, Any]) -> None:
        self._publish_child_event(
            make_directive_issued_event(directive),
            self._generation,
        )

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
                        if (
                            not self._playback_running
                            and not self._playback_processing
                        ):
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
            self._append_output_locked("boundary_flow_data", {
                "type": "boundary_flow_data",
                "label": "四边界预测流量",
                "event": data,
                "detail": boundary_flow_forecast_detail(observation),
                "workspace_id": active_workspace_id(),
            })
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
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "speed_changed",
                "label": "演进已自动降速",
                "detail": reason,
                "speed_multiplier": speed,
                "speed_phase": phase,
                "automatic": True,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()

    def _publish_policy_event(self, event: dict[str, Any]) -> None:
        event = {**event, "workspace_id": active_workspace_id()}
        if event.get("event_type") == "FloodForecastRequired":
            generation = self._begin_event_processing(event)
            self._publish_event(event, generation)
            return
        with self.condition:
            self._append_event_locked(event)
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
            self._clear_processing_locked()
            self._playback_phase = "finished"
            WORKSPACES.update_manifest(status="finished")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "finished",
                "label": "边界流量过程回放已结束",
                "detail": boundary_flow_forecast_detail(observation),
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()

    def _publish_event(
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
            self._append_event_locked(event)
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
            finally:
                self._complete_event_processing(event, generation)

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
            if generation != self._generation:
                return
            if source_id and source_id in self._published_inundation_sources:
                return
            if source_id:
                self._published_inundation_sources.add(source_id)
            if self._is_processing_event_locked(event, generation):
                self._processing_followup_pending = True
        self._publish_child_event(event, generation)

    def _begin_event_processing(self, event: dict[str, Any]) -> int:
        observation = (event.get("payload") or {}).get("observation") or {}
        simulation_time = str(
            observation.get("simulation_time")
            or observation.get("observed_at")
            or event.get("time")
            or "当前时刻"
        )
        with self.condition:
            generation = self._generation
            self._playback_running = False
            self._playback_paused = False
            self._playback_processing = True
            self._playback_phase = "processing"
            self._processing_generation = self._generation
            self._processing_event_id = str(event.get("event_id") or "")
            self._processing_correlation_id = str(
                event.get("correlation_id") or ""
            )
            self._processing_followup_pending = False
            WORKSPACES.update_manifest(status="processing")
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "processing",
                "label": "当前时刻事件处理中",
                "detail": (
                    f"演进已停在 {simulation_time}，等待洪水预测及其后续事件处理完成。"
                ),
                "automatic": True,
                "trigger_event_id": self._processing_event_id,
                "speed_multiplier": self._boundary_flow_runner.speed_multiplier,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()
            return generation

    def _complete_event_processing(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"FloodForecastRequired", "InundationGenerated"}:
            return
        with self.condition:
            if not self._is_processing_event_locked(event, generation):
                return
            if (
                event_type == "FloodForecastRequired"
                and self._processing_followup_pending
            ):
                return
            trigger_event_id = self._processing_event_id
            generated = event_type == "InundationGenerated"
            auto_pause = self._auto_pause_enabled
            self._playback_running = not auto_pause
            self._playback_paused = auto_pause
            self._clear_processing_locked()
            self._playback_phase = "paused" if auto_pause else "running"
            WORKSPACES.update_manifest(
                status="paused" if auto_pause else "active",
            )
            self._append_output_locked("runtime_status", {
                "type": "runtime_status",
                "status": "paused" if auto_pause else "running",
                "label": (
                    "当前时刻事件处理完成，演进已暂停"
                    if auto_pause
                    else "当前时刻事件处理完成，演进已继续"
                ),
                "detail": (
                    (
                        "洪水预测及其后续事件已处理完成；演进保持在当前时刻，"
                        "可继续演进或单步推进。"
                        if generated
                        else
                        "洪水预测事件已处理结束，但未生成有效的预测淹没事件；"
                        "演进保持在当前时刻，请检查事件 Trace 后决定是否继续。"
                    )
                    if auto_pause
                    else (
                        "洪水预测及其后续事件已处理完成；自动暂停已关闭，"
                        "演进继续播放下一时刻。"
                        if generated
                        else
                        "洪水预测事件已处理结束，但未生成有效的预测淹没事件；"
                        "自动暂停已关闭，演进继续播放下一时刻。"
                    )
                ),
                "automatic": True,
                "trigger_event_id": trigger_event_id,
                "workspace_id": active_workspace_id(),
            })
            self.condition.notify_all()

    def _is_processing_event_locked(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> bool:
        if (
            not self._playback_processing
            or generation != self._generation
            or generation != self._processing_generation
        ):
            return False
        correlation_id = str(event.get("correlation_id") or "")
        return (
            not self._processing_correlation_id
            or not correlation_id
            or correlation_id == self._processing_correlation_id
        )

    def _clear_processing_locked(self) -> None:
        self._playback_processing = False
        self._processing_generation = None
        self._processing_event_id = ""
        self._processing_correlation_id = ""
        self._processing_followup_pending = False

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
            self._append_event_locked(event)
            self._append_output_locked("agent_trace", {
                "type": "agent_trace",
                "tag": "EVENT",
                "label": event["title"],
                "detail": domain_event_detail(event),
                "event_id": event["event_id"],
            })
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
            self._append_output_locked(event_name, data)
            self.condition.notify_all()

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._append_output_locked("domain_event", event)

    def _append_output_locked(
        self,
        event_name: str,
        data: dict[str, Any],
    ) -> None:
        workspace_id = data.get("workspace_id") or active_workspace_id()
        if event_name == "runtime_status":
            playback_status = self._boundary_flow_runner.status()
            data = {
                **playback_status,
                "running": self._playback_running,
                "paused": self._playback_paused,
                "processing": self._playback_processing,
                "auto_pause_enabled": self._auto_pause_enabled,
                "playback_phase": self._playback_phase,
                "step_available": (
                    self._playback_paused
                    and playback_status.get("policy_state") != "PENDING"
                    and bool(playback_status.get("has_next"))
                ),
                **data,
            }
        item = {
            "event": event_name,
            "data": {**data, "workspace_id": workspace_id},
        }
        self.outputs.append(item)
        if not workspace_id:
            return
        path = (
            WORKSPACES.path(str(workspace_id), create=True)
            / "events"
            / "timeline.jsonl"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False, default=str))
                stream.write("\n")
        except OSError:
            # Persistence must not interrupt the live SSE stream.
            return
