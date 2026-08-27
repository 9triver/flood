"""dos flood host for the demo server: one kernel, all devices, all processes.

Replaces the legacy DomainRuntimeHost: hosts a dos kernel in the server
process (pump thread + durable journal), assembles the full flood world
(telemetry stations, forecast/impact compute devices, assets, assessments,
standing processes), and drives boundary-flow playback by republishing the
scenario CSV as telemetry frames — pause/step/speed are feeder controls,
exactly like a flight-simulator time knob, not kernel concepts.

Run the server with --dos to mount this host (legacy stays the default
until the flip).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dos import Journal, Kernel, mount_assessments, spawn_observer_watchdog
from dos.gateway import DosGateway
from dos.mqtt import InMemoryMqttBus
from domains.flood.dos_assets import mount_flood_assets
from domains.flood.dos_forecast import (
    BOUNDARIES,
    mount_forecast,
    real_cnn_runner,
    spawn_forecast_trigger,
)
from domains.flood.dos_impact import mount_impact, spawn_impact_auto
from domains.flood.dos_instance import STATION, build_mqtt_kernel, spawn_monitor
from domains.flood.runtime.boundary_flow import load_boundary_flow_rows

PREFIX = "water"
PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_JOURNAL = PROJECT_DIR / "local" / "runtime" / "dos" / "server-journal.jsonl"
ASSESSMENTS = "/hydro/shanhu/assessments"


def fake_impact_runner(args: dict, target: Path, forecast_meta: dict) -> dict:
    return {
        "summary": {"affected": 0, "by_risk": {}},
        "highlights": [],
        "artifacts": {"geojson": str(target / "impact.geojson")},
    }


class DosFloodHost:
    """Owns the kernel lifecycle and the boundary-flow playback knob."""

    def __init__(self, *, journal_path: Optional[Path] = None, fake_model: bool = False):
        journal_path = Path(journal_path) if journal_path else DEFAULT_JOURNAL
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        from dos.persistence import JsonlSink, load_journal, recover

        if journal_path.exists():
            journal = load_journal(str(journal_path))
            journal.attach_sink(JsonlSink(str(journal_path)))  # keep appending on reboot
        else:
            journal = Journal(clock=time.time, sink=JsonlSink(str(journal_path)))
        self.bus = InMemoryMqttBus()
        self.kernel = build_mqtt_kernel(
            self.bus, {STATION, *BOUNDARIES}, topic_prefix=PREFIX, journal=journal
        )
        mount_forecast(self.kernel, fake_forecast_runner if fake_model else real_cnn_runner)
        mount_impact(self.kernel, fake_impact_runner)
        mount_assessments(self.kernel, ASSESSMENTS)
        mount_flood_assets(self.kernel)
        self.gateway = DosGateway(self.kernel)

        self._monitor_cap = self.kernel.grant(f"/hydro/shanhu/stations/{STATION}", {"set_sampling_interval"}, "server")
        self._forecast_cap = self.kernel.grant("/hydro/shanhu/forecasts", {"run_forecast"}, "server")
        self._impact_cap = self.kernel.grant("/hydro/shanhu/impacts", {"analyze_impact"}, "server")
        self._assessment_cap = self.kernel.grant(ASSESSMENTS, {"file_assessment"}, "server")
        spawn_monitor(self.kernel, self._monitor_cap.token)
        spawn_forecast_trigger(self.kernel, self._forecast_cap.token)
        spawn_impact_auto(self.kernel, self._impact_cap.token, targets=[{"type": "bridge", "id": "*"}])
        spawn_observer_watchdog(
            self.kernel, self._assessment_cap.token, self.gateway,
            expected={}, check_every=60.0, assessments_base=ASSESSMENTS,
        )

        if journal_path.exists():
            recover(self.kernel)  # boots from the durable journal

        # playback knob
        self._rows = load_boundary_flow_rows()
        self._lock = threading.Lock()
        self._phase = "ready"
        self._index = 0
        self._speed = 1.0
        self._stop = threading.Event()
        self._wakeup = threading.Event()

        self._pump = threading.Thread(target=self.kernel.run, kwargs={"idle_seconds": 0.05}, daemon=True, name="dos-pump")
        self._feeder = threading.Thread(target=self._playback_loop, daemon=True, name="dos-playback")
        self._started = False

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._pump.start()
        self._feeder.start()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()
        self.kernel.stop()

    # ------------------------------------------------------------- playback

    def _publish_row(self, index: int) -> None:
        row = self._rows[index]
        observed_at = str(row["observed_at"])
        for boundary in BOUNDARIES:
            flow = float(((row.get("boundaries") or {}).get(boundary) or {}).get("flow_m3s") or 0)
            frame = json.dumps(
                {
                    "message_id": f"pb-{index}-{boundary}",
                    "observed_at": observed_at,
                    "metrics": {"flow_m3s": {"value": flow, "unit": "m3/s"}},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self.bus.inject(f"{PREFIX}/stations/{boundary}/telemetry", frame)
        # drain synchronously: every row must be an observable world state,
        # or fast playback swallows intermediate crossings (the trigger only
        # sees the batch's final window)
        self.kernel.pump()

    def _playback_loop(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(0.5 / max(self._speed, 0.01))
            self._wakeup.clear()
            if self._stop.is_set():
                return
            with self._lock:
                playing = self._phase == "playing" and self._index < len(self._rows)
                if playing:
                    self._publish_row(self._index)
                    self._index += 1
                    if self._index >= len(self._rows):
                        self._phase = "paused"

    def playback_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "playback_phase": self._phase,
                "sequence": self._index,
                "rows_total": len(self._rows),
                "speed_multiplier": self._speed,
                "workspace_id": "dos",
            }

    def start_playback(self, speed_multiplier: float = 20, source_id=None, **_) -> dict[str, Any]:
        with self._lock:
            if speed_multiplier:
                self._speed = max(0.1, float(speed_multiplier))
            self._phase = "playing"
            self._wakeup.set()
        return self.playback_status()

    def pause_playback(self, **_) -> dict[str, Any]:
        with self._lock:
            if self._phase == "playing":
                self._phase = "paused"
        return self.playback_status()

    def resume_playback(self, speed_multiplier: float = 1.0, **_) -> dict[str, Any]:
        with self._lock:
            self._speed = float(speed_multiplier) or self._speed
            self._phase = "playing"
            self._wakeup.set()
        return self.playback_status()

    def step_playback(self, **_) -> dict[str, Any]:
        with self._lock:
            if self._index < len(self._rows):
                self._publish_row(self._index)
                self._index += 1
        return self.playback_status()

    def set_playback_speed(self, speed_multiplier: float, **_) -> dict[str, Any]:
        with self._lock:
            self._speed = max(0.1, float(speed_multiplier or 1.0))
            self._wakeup.set()
        return self.playback_status()

    def stop_playback(self, **_) -> dict[str, Any]:
        with self._lock:
            self._phase = "stopped"
        return self.playback_status()

    def restart_playback(self, speed_multiplier: float = 20, source_id=None, **_) -> dict[str, Any]:
        with self._lock:
            if speed_multiplier:
                self._speed = max(0.1, float(speed_multiplier))
            self._index = 0
            self._phase = "playing"
            self._wakeup.set()
        return self.playback_status()

    def set_auto_pause(self, enabled: bool, **_) -> dict[str, Any]:
        return {"auto_pause": bool(enabled)}


class DosPlaybackController:
    """Same surface as DomainPlaybackController, driving the dos feeder."""

    def __init__(self, host: DosFloodHost, sources) -> None:
        self.host = host
        self.sources = sources

    def status(self) -> dict[str, Any]:
        status = self.host.playback_status()
        latest = self.host.kernel.namespace.try_read("/hydro/shanhu/forecasts/latest")
        status["domain_os"] = "dos"
        status["latest_forecast"] = (latest.value if latest else None)
        return status

    def list_playback_sources(self) -> dict[str, Any]:
        return self.sources.list()

    def upload_playback_source(self, filename: str, content: bytes) -> dict[str, Any]:
        return self.sources.register(filename, content)

    def stream(self, interval: float = 2.0):
        import json as _json

        while True:
            yield b"data: " + _json.dumps(self.status(), ensure_ascii=False, default=str).encode() + b"\n\n"
            time.sleep(interval)

    def __getattr__(self, name):
        return getattr(self.host, name)


def fake_forecast_runner(args: dict, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    (target / "depth_series.npy").write_bytes(b"fake")
    return {
        "stats": {"wet_cells": 1234, "max_depth_m": 2.0},
        "artifacts": {"depth_series": str(target / "depth_series.npy"), "max_depth_csv": str(target / "max_depth.csv")},
        "model": {"model_name": "FAKE"},
    }
