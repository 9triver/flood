"""Flood forecast on the dos kernel: a compute device plus a stateless
trigger process over the observation mirror.

Pipeline (production shape):

    boundary stations (MQTT telemetry: flow_m3s, observed_at)
      → journal + mirror + namespace                      (kernel plane)
      → forecast-trigger process                          (user space)
          mirror.query(24h window) → total > 230 m³/s
          → act("run_forecast", args=input snapshot)      (journaled audit)
      → ForecastDevice (compute device, mounted /hydro/shanhu/forecasts)
          worker thread runs the model runner
          self-interrupts job_started / job_done / job_error
      → fsck commits the transaction on *fresh* job evidence
      → namespace: forecasts/{id} metadata + latest pointer
                   (big artifacts stay on disk; namespace holds handles)

Design notes:

- The trigger process holds **no private state**: every decision input is
  re-derived from mirror queries, so it survives restarts for free.
  Re-trigger policy: skip while a job is pending unless newer observations
  arrived after the pending job's input.
- forecast identity comes from the journal (``fcst_{dispatched_seq}``) —
  unique, ordered, auditable; no extra epoch machinery.
- Runners are pluggable: tests inject an instant fake; production wires
  ``run_cnn_v2_forecast`` (see scripts/check_dos_forecast.py --real).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from dos import Driver, Kernel, ProcessSpec
from dos.devices import PendingTxn

from .runtime.boundary_flow import (
    BOUNDARIES,
    FORECAST_TRIGGER_TOTAL_M3S,
    FORECAST_WINDOW_HOURS,
)

FORECAST_MOUNT = "/hydro/shanhu/forecasts"
PENDING_PATH = f"{FORECAST_MOUNT}/pending"
LAST_JOB_PATH = f"{FORECAST_MOUNT}/last_job"
LATEST_PATH = f"{FORECAST_MOUNT}/latest"
RUN_FORECAST = "run_forecast"
FORECAST_DEADLINE_S = 30 * 60.0

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = PROJECT_DIR / "local" / "runtime" / "dos" / "forecasts"

ForecastRunner = Callable[[dict, Path], dict]


class ForecastDevice(Driver):
    """The flood model as a device: a job is a command, its completion is
    telemetry, and the transaction commits only on fresh job evidence."""

    device_id = "compute:flood-cnn-v2"
    default_txn_timeout = 60.0

    def __init__(self, runner: Optional[ForecastRunner] = None, artifact_root: Optional[Path] = None):
        self.runner = runner
        self.artifact_root = Path(artifact_root) if artifact_root else DEFAULT_ARTIFACT_ROOT
        self.jobs: dict[str, dict] = {}  # txn_id -> job record (introspection)
        self.last_error: Optional[str] = None

    def deadline_for(self, action: str) -> Optional[float]:
        return FORECAST_DEADLINE_S if action == RUN_FORECAST else None

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        if action != RUN_FORECAST:
            return f"unsupported action: {action}"
        stations = args.get("stations")
        if not isinstance(stations, dict) or not stations:
            return "args.stations must be a non-empty map of boundary -> series"
        for key, series in stations.items():
            if not isinstance(series, list) or not series:
                return f"args.stations[{key}] must be a non-empty series"
        if not isinstance(args.get("window_hours"), (int, float)) or args["window_hours"] <= 0:
            return "args.window_hours must be positive"
        return None

    # -------------------------------------------------------------- downlink

    def dispatch(self, txn: PendingTxn) -> None:
        if self.runner is None:
            raise RuntimeError("ForecastDevice has no runner configured")
        forecast_id = f"fcst_{txn.dispatched_seq:06d}"
        target = self.artifact_root / forecast_id
        self.jobs[txn.txn_id] = {"forecast_id": forecast_id, "target": str(target)}
        self.kernel.interrupt(
            self.device_id,
            {"kind": "job_started", "txn_id": txn.txn_id, "input_last_observed_at": txn.args.get("last_observed_at")},
        )
        threading.Thread(
            target=self._run_job,
            args=(txn, forecast_id, target),
            daemon=True,
            name=f"dos-{self.device_id}-{forecast_id}",
        ).start()

    def _run_job(self, txn: PendingTxn, forecast_id: str, target: Path) -> None:
        args = txn.args
        try:
            result = self.runner(args, target)
        except Exception as exc:  # noqa: BLE001 — job failure is evidence, not a crash
            self.kernel.interrupt(self.device_id, {"kind": "job_error", "txn_id": txn.txn_id, "error": f"{type(exc).__name__}: {exc}"})
            return
        self.kernel.interrupt(
            self.device_id,
            {
                "kind": "job_done",
                "txn_id": txn.txn_id,
                "forecast_id": forecast_id,
                "input_last_observed_at": args.get("last_observed_at"),
                "input_summary": {
                    "total_m3s": args.get("total_m3s"),
                    "window_hours": args.get("window_hours"),
                    "boundaries": sorted((args.get("stations") or {}).keys()),
                },
                "result": result,
            },
        )

    # ------------------------------------------------------------ interrupts

    def normalize(self, raw: object) -> Iterable[tuple]:
        kind = raw["kind"]
        if kind == "job_started":
            yield (
                PENDING_PATH,
                {"txn_id": raw["txn_id"], "input_last_observed_at": raw.get("input_last_observed_at"), "started_at": time.time()},
                time.time(),
            )
        elif kind == "job_done":
            forecast_id = raw["forecast_id"]
            meta = dict(raw["result"])
            meta.update(
                {
                    "id": forecast_id,
                    "txn_id": raw["txn_id"],
                    "valid_from": raw.get("input_last_observed_at"),
                    "input": raw.get("input_summary") or {},
                }
            )
            yield PENDING_PATH, {}, time.time()
            yield LAST_JOB_PATH, {"txn_id": raw["txn_id"], "status": "done", "forecast_id": forecast_id}, time.time()
            yield f"{FORECAST_MOUNT}/{forecast_id}", meta, time.time()
            yield LATEST_PATH, {"id": forecast_id}, time.time()
        elif kind == "job_error":
            yield PENDING_PATH, {}, time.time()
            yield LAST_JOB_PATH, {"txn_id": raw["txn_id"], "status": "error", "error": raw["error"]}, time.time()
        else:
            self.last_error = f"unknown job frame: {kind}"

    # ------------------------------------------------------------ fsck rule

    def verify(self, txn: PendingTxn, read) -> str:
        snap = read(LAST_JOB_PATH)
        if snap is None:
            return "pending"
        evidence = snap.value
        if evidence.get("txn_id") != txn.txn_id:
            return "pending"  # another job's evidence; our deadline decides
        if evidence.get("status") == "done":
            return "committed"
        if evidence.get("status") == "error":
            return f"failed: {evidence.get('error') or 'model job failed'}"
        return "pending"


def real_cnn_runner(args: dict, target: Path) -> dict:
    """Production runner: mirror-query input snapshot → legacy CNN_V2 format.

    CNN failures return ``{"error": ...}`` without raising — translated
    here into an exception so the job transaction fails explicitly."""
    from datetime import datetime, timezone

    from .runtime.cnn_v2 import run_cnn_v2_forecast

    stations = args["stations"]
    window_start = min(pairs[0][0] for pairs in stations.values())
    boundaries = {}
    for boundary, pairs in stations.items():
        series = [
            {"time_h": round((ts - window_start) / 3600.0, 3), "flow_m3s": round(float(value), 6)}
            for ts, value in pairs
        ]
        values = [point["flow_m3s"] for point in series]
        boundaries[boundary] = {"label": boundary, "point_count": len(series), "series": series, "peak_flow_m3s": max(values)}
    summary = {
        "boundary_flow_id": f"dos_{target.name}",
        "mode": "dos_mirror_query",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat(),
        "forecast_point_count": max(b["point_count"] for b in boundaries.values()),
        "forecast_horizon_h": args.get("window_hours", 24),
        "rainfall_total_mm": 0.0,
        "rainfall_series": [],
        "reservoir_level_m": 0.0,
        "boundaries": boundaries,
    }
    target.mkdir(parents=True, exist_ok=True)
    result = run_cnn_v2_forecast({"summary": summary}, target / "max_depth.csv", working_dir=target / "_work")
    if result.get("error"):
        raise RuntimeError(f"{result['error']}: {result.get('detail') or result.get('stderr_tail') or ''}")
    wet = result.get("wet_cells") or result.get("positive_cells") or len(result.get("_positive_depths") or [])
    return {
        "stats": {"max_depth_m": result.get("max_depth_m"), "wet_cells": wet, "time_step_count": result.get("time_step_count")},
        "artifacts": {
            "max_depth_csv": str(target / "max_depth.csv"),
            "depth_series": str(target / "depth_series.npy"),
            "time_steps": str(target / "time_steps.json"),
        },
        "model": {"model_name": result.get("model_name"), "device": result.get("device"), "timings_ms": result.get("timings_ms")},
    }


# ------------------------------------------------------------------ assembly


def mount_forecast(kernel: Kernel, runner: ForecastRunner, artifact_root: Optional[Path] = None) -> ForecastDevice:
    device = ForecastDevice(runner=runner, artifact_root=artifact_root)
    kernel.mount(FORECAST_MOUNT, device)
    return device


def boundary_flow_path(boundary: str) -> str:
    return f"/hydro/shanhu/stations/{boundary}/flow_m3s"


def spawn_forecast_trigger(
    kernel: Kernel,
    cap_token: str,
    *,
    boundaries: Optional[tuple[str, ...]] = None,
    threshold_m3s: float = FORECAST_TRIGGER_TOTAL_M3S,
    window_hours: float = FORECAST_WINDOW_HOURS,
    sink: Optional[list] = None,
) -> None:
    """Stateless trigger: every decision input comes from the mirror."""
    boundaries = tuple(boundaries) if boundaries else tuple(BOUNDARIES)
    events = sink if sink is not None else []

    def handler(ctx):
        world_now = None
        series = {}
        for boundary in boundaries:
            samples = ctx.history(boundary_flow_path(boundary))
            if not samples:
                return  # a boundary has never reported yet
            series[boundary] = [(s.observed_at, s.value) for s in samples]
            boundary_now = series[boundary][-1][0]
            world_now = boundary_now if world_now is None else max(world_now, boundary_now)
        if world_now is None:
            return
        since = world_now - window_hours * 3600.0
        windowed = {
            boundary: [(ts, value) for ts, value in pairs if ts >= since]
            for boundary, pairs in series.items()
        }
        peak_total = _peak_total(windowed)
        if peak_total is None or peak_total <= threshold_m3s:
            return
        newest_observed_at = max(pairs[-1][0] for pairs in windowed.values())
        pending_snap = ctx.try_read(PENDING_PATH)
        pending = pending_snap.value if pending_snap else {}
        if pending.get("txn_id") and (pending.get("input_last_observed_at") or 0) >= newest_observed_at:
            return  # in-flight job already covers this (or newer) data
        latest_snap = ctx.try_read(LATEST_PATH)
        if latest_snap is not None:
            covered = ctx.try_read(f"{FORECAST_MOUNT}/{latest_snap.value.get('id')}")
            if covered is not None and (covered.value.get("valid_from") or 0) >= newest_observed_at:
                return  # the committed forecast already covers this (or newer) data
        result = ctx.act(
            cap_token,
            LATEST_PATH,
            RUN_FORECAST,
            {
                "stations": {b: [[ts, v] for ts, v in pairs] for b, pairs in windowed.items()},
                "window_hours": window_hours,
                "total_m3s": peak_total,
                "last_observed_at": newest_observed_at,
            },
        )
        events.append(f"run_forecast->{result.state}" + ("(reused)" if result.reused else ""))

    kernel.spawn(
        ProcessSpec(
            name="forecast-trigger",
            watches=tuple(boundary_flow_path(b) for b in boundaries),
            handler=handler,
            priority=5,
            budget_seconds=5.0,
            description="边界流量窗口超阈 → 发起洪水预测（无状态，全部依据来自镜像）",
        )
    )


def _peak_total(windowed: dict) -> Optional[float]:
    """Peak aligned instantaneous total across boundaries (fallback: sum of
    latest values when stations are not aligned on shared timestamps)."""
    per_boundary_at: dict[float, list[float]] = {}
    latest: dict[str, float] = {}
    for boundary, pairs in windowed.items():
        latest[boundary] = pairs[-1][1]
        for ts, value in pairs:
            per_boundary_at.setdefault(ts, []).append(value)
    aligned = [
        sum(values)
        for ts, values in per_boundary_at.items()
        if len(values) == len(windowed)
    ]
    if aligned:
        return max(aligned)
    if latest:
        return sum(latest.values())
    return None
