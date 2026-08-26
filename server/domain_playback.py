"""Time controls and UI progress for Domain OS flood evolution."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from domains.flood.forecast_domain import (
    FORECAST_FAILED_EVENT,
    FORECAST_GENERATED_EVENT,
    FORECAST_REQUIRED_EVENT,
)
from domains.flood.runtime.boundary_flow import (
    build_boundary_flow_observation,
    load_boundary_flow_rows,
)
from domains.flood.runtime.playback_sources import PlaybackSourceRegistry
from server.domain_runtime_host import DomainRuntimeHost
from server.events.playback import PLAYBACK_SPEEDS
from server.serialization import format_sse


class PlaybackDomainSystem(Protocol):
    runtime: Any

    async def advance(self) -> dict[str, Any] | None: ...

    def evolution_status(self) -> dict[str, Any]: ...

    def reset_evolution(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        run_id: str | None = None,
        source_ref: str = "boundary-flow-scenario",
    ) -> dict[str, Any]: ...


class DomainPlaybackController:
    """Schedule evolution on the DomainRuntimeHost owner event loop."""

    def __init__(
        self,
        host: DomainRuntimeHost,
        playback_sources: PlaybackSourceRegistry | None = None,
        *,
        base_interval_seconds: float = 5.0,
    ) -> None:
        self.host = host
        self.playback_sources = playback_sources or PlaybackSourceRegistry()
        self.base_interval_seconds = float(base_interval_seconds)
        self.condition = threading.Condition()
        self.outputs: list[dict[str, Any]] = []

        source = self.playback_sources.get(
            self.playback_sources.selected_source_id
        )
        self._current_source = source.public(selected=True)
        self._rows: tuple[dict[str, Any], ...] = ()
        self._prepared = False
        self._started = False
        self._closed = False
        self._worker_task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._generation = 0
        self._speed_multiplier = 20.0
        self._auto_pause_enabled = True
        self._playback_phase = "ready"
        self._last_observation: dict[str, Any] | None = None
        self._domain_status: dict[str, Any] = {
            "evolution_run_id": None,
            "sequence": None,
            "next_sequence": 0,
            "total_rows": source.row_count,
            "has_next": True,
            "next_step_forecast_triggered": False,
        }
        self._forecast_version = 0
        self._completed_forecast_version = 0
        self._event_count = 0

    def ensure_started(self) -> None:
        self.host.call_system(self._ensure_worker)

    def start_playback(
        self,
        speed_multiplier: float = 20.0,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        speed = _playback_speed(speed_multiplier)
        source, rows = self._source_rows(source_id)

        async def start(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase == "processing":
                raise ValueError("当前领域产品仍在生成，不能开始新的演进")
            if self._playback_phase == "running" and source_id is None:
                return self._status()
            if (
                not self._prepared
                or source_id is not None
                or self._playback_phase in {"finished", "stopped"}
            ):
                self._reset_domain(system, source, rows)
                self.outputs.clear()
            self._speed_multiplier = speed
            self._playback_phase = "running"
            self._append_runtime_status({
                "status": "running",
                "label": "Domain OS 演进已启动",
                "detail": (
                    f"领域运行时正以 {speed:g}× 速率按时间顺序推进边界流量事实。"
                ),
            })
            self._notify_worker()
            return self._status()

        result = self.host.call_system(start)
        self.playback_sources.select(source.source_id)
        return result

    def restart_playback(
        self,
        speed_multiplier: float = 20.0,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        speed = _playback_speed(speed_multiplier)
        source, rows = self._source_rows(source_id)

        async def restart(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase == "processing":
                raise ValueError("当前领域产品仍在生成，不能重置演进")
            self._generation += 1
            self._reset_domain(system, source, rows)
            self.outputs.clear()
            self._speed_multiplier = speed
            self._playback_phase = "ready"
            self._append_runtime_status({
                "status": "reset",
                "label": "Domain OS 演进已重置",
                "detail": "已创建新的领域演进 run，并回到第一个边界流量时刻。",
            })
            self._notify_worker()
            return {**self._status(), "status": "reset"}

        result = self.host.call_system(restart)
        self.playback_sources.select(source.source_id)
        return result

    def stop_playback(self) -> dict[str, Any]:
        async def stop(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase in {"ready", "finished", "stopped"}:
                return self._status()
            self._generation += 1
            self._playback_phase = "stopped"
            self._append_runtime_status({
                "status": "stopped",
                "label": "Domain OS 演进已停止",
                "detail": "后台不再推进新的边界流量事实。",
            })
            self._notify_worker()
            return self._status()

        return self.host.call_system(stop)

    def pause_playback(self) -> dict[str, Any]:
        async def pause(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase != "running":
                return self._status()
            self._playback_phase = "paused"
            self._append_runtime_status({
                "status": "paused",
                "label": "Domain OS 演进已暂停",
                "detail": "后台已暂停写入新的边界流量事实。",
            })
            self._notify_worker()
            return {**self._status(), "status": "paused"}

        return self.host.call_system(pause)

    def resume_playback(self, speed_multiplier: float = 1.0) -> dict[str, Any]:
        speed = _playback_speed(speed_multiplier)

        async def resume(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase == "running":
                return self._status()
            if self._playback_phase != "paused":
                raise ValueError("boundary flow playback is not paused")
            self._speed_multiplier = speed
            self._playback_phase = "running"
            self._append_runtime_status({
                "status": "running",
                "label": "Domain OS 演进已继续",
                "detail": "后台从暂停位置继续推进边界流量事实。",
            })
            self._notify_worker()
            return {**self._status(), "status": "running"}

        return self.host.call_system(resume)

    def step_playback(self) -> dict[str, Any]:
        async def step(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            if self._playback_phase != "paused":
                raise ValueError("演进当前不在暂停状态")
            generation = self._generation
            stepped, forecast_triggered = await self._advance_once(
                system,
                generation,
                manual=True,
            )
            status = "finished" if not stepped else "stepped"
            return {
                **self._status(),
                "status": status,
                "stepped": stepped,
                "forecast_triggered": forecast_triggered,
            }

        return self.host.call_system(step)

    def set_playback_speed(self, speed_multiplier: float) -> dict[str, Any]:
        speed = _playback_speed(speed_multiplier)

        async def set_speed(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            self._speed_multiplier = speed
            self._append_runtime_status({
                "status": "speed_changed",
                "label": "Domain OS 演进速率已调整",
                "detail": f"边界流量事实推进速率调整为 {speed:g}×。",
            })
            self._notify_worker()
            return self._status()

        return self.host.call_system(set_speed)

    def set_auto_pause(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("auto_pause_enabled must be a boolean")

        async def set_value(system: PlaybackDomainSystem) -> dict[str, Any]:
            await self._ensure_worker(system)
            self._auto_pause_enabled = enabled
            self._append_runtime_status({
                "status": "auto_pause_changed",
                "label": "自动暂停已开启" if enabled else "自动暂停已关闭",
                "detail": (
                    "预测与影响产品生成后，演进将自动暂停。"
                    if enabled
                    else "预测与影响产品生成后，演进将继续推进。"
                ),
            })
            return {**self._status(), "status": "auto_pause_changed"}

        return self.host.call_system(set_value)

    def status(self) -> dict[str, Any]:
        with self.condition:
            return self._status()

    def list_playback_sources(self) -> dict[str, Any]:
        return self.playback_sources.list_sources()

    def upload_playback_source(
        self,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        source = self.playback_sources.upload(filename, content)
        return {"source": source.public(selected=False)}

    def stream(self, interval: int) -> Iterator[bytes]:
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
                    heartbeat = {
                        **self._status(),
                        "type": "runtime_status",
                        "label": "等待启动 Domain OS 演进",
                        "detail": "播放控制只调度领域运行时，不生成旧自治事件。",
                    }
                else:
                    pending = self.outputs[next_seq:]
                    next_seq = len(self.outputs)
            if heartbeat is not None:
                yield format_sse("runtime_status", heartbeat)
            for item in pending:
                yield format_sse(item["event"], item["data"])

    def close(self) -> None:
        async def close_controller(system: PlaybackDomainSystem) -> None:
            self._closed = True
            self._generation += 1
            self._notify_worker()
            task = self._worker_task
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            self._worker_task = None

        self.host.call_system(close_controller)

    async def _ensure_worker(self, system: PlaybackDomainSystem) -> None:
        if self._closed:
            raise RuntimeError("domain playback controller is closed")
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._wake = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._worker(system),
            name="domain-playback-controller",
        )
        self._started = True

    async def _worker(self, system: PlaybackDomainSystem) -> None:
        while not self._closed:
            if self._playback_phase != "running":
                await self._wait_for_wake()
                continue
            generation = self._generation
            await self._advance_once(system, generation, manual=False)
            if self._playback_phase != "running" or generation != self._generation:
                continue
            try:
                await asyncio.wait_for(
                    self._wait_for_wake(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass

    async def _advance_once(
        self,
        system: PlaybackDomainSystem,
        generation: int,
        *,
        manual: bool,
    ) -> tuple[bool, bool]:
        domain_status = system.evolution_status()
        if not domain_status.get("has_next"):
            self._finish()
            return False, False

        forecast_expected = bool(
            domain_status.get("next_step_forecast_triggered")
        )
        if forecast_expected:
            self._playback_phase = "processing"
            self._append_runtime_status({
                "status": "processing",
                "label": "Domain OS 正在生成领域产品",
                "detail": "当前时刻已触发预测，正在生成洪水预测与影响评估产品。",
                "automatic": not manual,
            })

        runtime = system.runtime
        event_offset = len(runtime.events())
        row = await system.advance()
        if row is None:
            if generation == self._generation:
                self._finish()
            return False, False
        if generation != self._generation:
            return True, forecast_expected

        self._domain_status = system.evolution_status()
        self._event_count = len(runtime.events())
        sequence = int(row.get("sequence", self._domain_status["sequence"]))
        observation = build_boundary_flow_observation(
            self._rows,
            sequence,
            playback_id=str(self._domain_status.get("evolution_run_id") or ""),
        )
        self._last_observation = observation
        self._append_output("boundary_flow_data", {
            "type": "boundary_flow_data",
            "label": "Domain OS 边界流量事实",
            "detail": (
                f"已推进到 {observation.get('simulation_time') or observation.get('observed_at')}，"
                f"四边界总流量 {float(observation.get('total_flow_m3s') or 0):.3f} m3/s。"
            ),
            "event": {
                "type": "domain_evolution_progress",
                "event_id": (
                    f"evolution_{self._domain_status.get('evolution_run_id')}_{sequence:04d}"
                ),
                "event_type": "water.flood.evolution.advanced",
                "source_type": "water.evolution-source",
                "source_id": "water.evolution-source/boundary-flow",
                "time": observation["observed_at"],
                "payload": {"observation": observation},
                "correlation_id": self._domain_status.get("evolution_run_id"),
            },
        })

        new_events = runtime.events()[event_offset:]
        event_types = {event.event_type for event in new_events}
        forecast_triggered = FORECAST_REQUIRED_EVENT in event_types
        if forecast_triggered:
            self._forecast_version += 1
        generated = FORECAST_GENERATED_EVENT in event_types
        failed = FORECAST_FAILED_EVENT in event_types
        if generated:
            self._completed_forecast_version = self._forecast_version

        if not self._domain_status.get("has_next"):
            self._finish()
        elif manual or (forecast_triggered and self._auto_pause_enabled):
            self._playback_phase = "paused"
            self._append_runtime_status({
                "status": "paused" if forecast_triggered else "stepped",
                "label": (
                    "领域产品已生成，演进已暂停"
                    if generated
                    else "领域预测失败，演进已暂停"
                    if failed
                    else "Domain OS 演进已单步推进"
                ),
                "detail": (
                    "洪水预测及影响评估已经落为不可变产品。"
                    if generated
                    else "当前边界流量时刻已写入领域投影。"
                ),
                "forecast_triggered": forecast_triggered,
            })
        else:
            self._playback_phase = "running"
            if forecast_triggered:
                self._append_runtime_status({
                    "status": "running",
                    "label": "领域产品已生成，演进已继续",
                    "detail": "预测与影响评估完成，继续推进下一时刻。",
                    "forecast_triggered": True,
                })
        return True, forecast_triggered

    def _reset_domain(
        self,
        system: PlaybackDomainSystem,
        source: Any,
        rows: Sequence[dict[str, Any]],
    ) -> None:
        self._generation += 1
        run_id = f"domain-playback-{source.source_id}-{uuid4().hex[:12]}"
        self._domain_status = system.reset_evolution(
            rows,
            run_id=run_id,
            source_ref=f"playback-source:{source.source_id}",
        )
        self._rows = tuple(dict(row) for row in rows)
        self._current_source = source.public(selected=True)
        self._prepared = True
        self._last_observation = None
        self._forecast_version = 0
        self._completed_forecast_version = 0

    def _finish(self) -> None:
        self._playback_phase = "finished"
        self._append_runtime_status({
            "status": "finished",
            "label": "Domain OS 演进已结束",
            "detail": "配置的边界流量事实已经全部推进完成。",
        })

    def _source_rows(self, source_id: str | None) -> tuple[Any, list[dict[str, Any]]]:
        selected_id = source_id or self._current_source.get("source_id")
        source = self.playback_sources.get(str(selected_id or ""))
        return source, load_boundary_flow_rows(Path(source.csv_path))

    @property
    def interval_seconds(self) -> float:
        return self.base_interval_seconds / self._speed_multiplier

    def _status(self) -> dict[str, Any]:
        phase = self._playback_phase
        running = phase == "running"
        paused = phase == "paused"
        processing = phase == "processing"
        return {
            "runtime_mode": "domain_os",
            "running": running,
            "paused": paused,
            "processing": processing,
            "auto_pause_enabled": self._auto_pause_enabled,
            "playback_phase": phase,
            "started": self._started,
            "event_count": self._event_count,
            "output_count": len(self.outputs),
            "workspace_id": None,
            "playback_source": dict(self._current_source),
            "policy_state": "PENDING" if processing else "ACTIVE" if self._forecast_version else "NORMAL",
            "forecast_version": self._forecast_version,
            "completed_forecast_version": self._completed_forecast_version,
            "forecast_running": processing,
            "observed_at": (
                self._last_observation.get("observed_at")
                if self._last_observation
                else None
            ),
            "simulation_time": (
                self._last_observation.get("simulation_time")
                if self._last_observation
                else None
            ),
            "speed_multiplier": self._speed_multiplier,
            "interval_seconds": self.interval_seconds,
            **self._domain_status,
            "step_available": paused and bool(self._domain_status.get("has_next")),
        }

    def _append_runtime_status(self, data: dict[str, Any]) -> None:
        self._append_output("runtime_status", {
            **self._status(),
            "type": "runtime_status",
            **data,
        })

    def _append_output(self, event: str, data: dict[str, Any]) -> None:
        with self.condition:
            self.outputs.append({"event": event, "data": data})
            self.condition.notify_all()

    def _notify_worker(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _wait_for_wake(self) -> None:
        wake = self._wake
        if wake is None:
            return
        await wake.wait()
        wake.clear()


def _playback_speed(value: float) -> float:
    speed = float(value)
    if speed not in PLAYBACK_SPEEDS:
        raise ValueError("playback speed must be one of 1, 2, 5, 10, 20")
    return speed


__all__ = ["DomainPlaybackController", "PlaybackDomainSystem"]
