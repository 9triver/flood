from __future__ import annotations

import csv
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .common import DOMAIN_DATA_DIR, rel
from .workspace import workspace_dir


BOUNDARIES = {
    "interval1": "区间1",
    "interval2": "区间2",
    "tonggu": "同古河",
    "upstream": "坝址",
}

BASE_FLOWS_M3S = {
    "interval1": 0.256694,
    "interval2": 0.036155,
    "tonggu": 0.036155 * 0.946,
    "upstream": 0.220762,
}

DEFAULT_BOUNDARY_FLOW_CSV_PATH = DOMAIN_DATA_DIR / "mock" / "boundary_flow.csv"
FORECAST_WINDOW_HOURS = 24
FORECAST_WINDOW_POINT_COUNT = FORECAST_WINDOW_HOURS + 1
FORECAST_TRIGGER_TOTAL_M3S = 230.0
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
BOUNDARY_FLOW_TIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
)


def boundary_flow_runtime_dir(*, create: bool = False) -> Path:
    return workspace_dir(create=create) / "boundary_flows"


def latest_observations_path() -> Path:
    return boundary_flow_runtime_dir() / "observations" / "latest.jsonl"


def forecast_input_dir() -> Path:
    return boundary_flow_runtime_dir() / "forecast_inputs"


def latest_forecast_input_path() -> Path:
    return boundary_flow_runtime_dir() / "latest_forecast_input.json"


def configured_boundary_flow_csv_path() -> Path:
    configured = os.environ.get("FLOOD_BOUNDARY_FLOW_CSV")
    return Path(configured).expanduser() if configured else DEFAULT_BOUNDARY_FLOW_CSV_PATH


def load_boundary_flow_rows(path: Path | None = None) -> list[dict[str, Any]]:
    source_path = path or configured_boundary_flow_csv_path()
    rows: list[dict[str, Any]] = []
    with source_path.open(newline="", encoding="utf-8-sig") as file:
        for sequence, raw in enumerate(csv.DictReader(file)):
            observed_at = parse_boundary_flow_time(
                str(raw.get("time_period_end") or "")
            ).replace(tzinfo=CHINA_STANDARD_TIME)
            interval2 = _number(raw.get("interval2_outlet_flow_m3s"))
            boundaries = {
                "interval1": _boundary("interval1", raw.get("interval1_outlet_flow_m3s")),
                "interval2": _boundary("interval2", interval2),
                "tonggu": _boundary("tonggu", interval2 * 0.946),
                "upstream": _boundary("upstream", raw.get("release_m3s")),
            }
            baseflow_total = sum(BASE_FLOWS_M3S.values())
            rows.append({
                "sequence": sequence,
                "observed_at": observed_at.isoformat(),
                "simulation_time": observed_at.isoformat(),
                "rainfall_mm": round(_number(raw.get("rainfall_mm")), 3),
                "reservoir_inflow_m3s": round(_number(raw.get("reservoir_outlet_flow_m3s")), 6),
                "reservoir_release_m3s": round(_number(raw.get("release_m3s")), 6),
                "reservoir_level_m": round(_number(raw.get("end_level_m")), 3),
                "boundaries": boundaries,
                "baseflow_total_m3s": round(baseflow_total, 6),
                "total_flow_m3s": round(sum(item["flow_m3s"] for item in boundaries.values()), 6),
            })
    return rows


class BoundaryFlowPlaybackSource:
    """Replays the tracked boundary-flow process one observation at a time."""

    def __init__(self, csv_path: Path | None = None,
                 observation_path: Path | None = None):
        self.csv_path = csv_path or configured_boundary_flow_csv_path()
        self.observation_path = observation_path or latest_observations_path()
        self._workspace_observation_path = observation_path is None
        self.rows = load_boundary_flow_rows(self.csv_path)
        self.index = 0
        self.run_id = ""
        self.reset()

    def reset(self) -> None:
        if self._workspace_observation_path:
            self.observation_path = latest_observations_path()
        self.index = 0
        self.run_id = f"boundary_playback_{uuid.uuid4().hex[:10]}"
        if self.observation_path.exists():
            self.observation_path.unlink()

    def next_observation(self) -> dict[str, Any] | None:
        if self.index >= len(self.rows):
            return None
        observation = dict(self.rows[self.index])
        observation["playback_id"] = self.run_id
        self.index += 1
        self._append_observation(observation)
        return observation

    def _append_observation(self, observation: dict[str, Any]) -> None:
        self.observation_path.parent.mkdir(parents=True, exist_ok=True)
        with self.observation_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(observation, ensure_ascii=False) + "\n")


class FloodForecastPolicy:
    """Triggers CNN prediction from the current row's fixed 24-hour window."""

    NORMAL = "NORMAL"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"

    def __init__(self, reference_rows: list[dict[str, Any]], *,
                 forecast_input_dir: Path | None = None,
                 latest_forecast_input_path: Path | None = None,
                 total_trigger_m3s: float = FORECAST_TRIGGER_TOTAL_M3S):
        self.reference_rows = reference_rows
        self.forecast_input_dir = forecast_input_dir or globals()["forecast_input_dir"]()
        self.latest_forecast_input_path = (
            latest_forecast_input_path or globals()["latest_forecast_input_path"]()
        )
        self._workspace_forecast_paths = (
            forecast_input_dir is None and latest_forecast_input_path is None
        )
        self.total_trigger_m3s = total_trigger_m3s
        self.reset()

    def reset(self) -> None:
        if self._workspace_forecast_paths:
            self.forecast_input_dir = forecast_input_dir()
            self.latest_forecast_input_path = latest_forecast_input_path()
        self.state = self.NORMAL
        self.episode_id = ""
        self.last_observation: dict[str, Any] | None = None
        self.latest_forecast_input: dict[str, Any] | None = None
        self.version = 0
        self.completed_version = 0
        self.request_running = False

    def observe(
        self,
        observation: dict[str, Any],
        *,
        rolling: bool = False,
    ) -> list[dict[str, Any]]:
        self.last_observation = observation
        if self.state == self.PENDING:
            return []
        if self.state == self.ACTIVE and not rolling:
            return []
        if self.state not in {self.NORMAL, self.ACTIVE}:
            return []

        window = self._window_from(observation)
        if len(window) != FORECAST_WINDOW_POINT_COUNT:
            return []
        exceeded = [row for row in window if _total_flow(row) > self.total_trigger_m3s]
        if not exceeded:
            return []
        if not self.episode_id:
            simulation_time = _observed_datetime(observation)
            self.episode_id = f"flood_{simulation_time.strftime('%Y%m%dT%H%M')}"
        peak = max(window, key=_total_flow)
        reason_prefix = "人工步进后，" if rolling and self.completed_version else ""
        reason = reason_prefix + (
            f"当前时刻至 +{FORECAST_WINDOW_HOURS}h 的预测窗口内，"
            f"四边界流量和峰值 {_total_flow(peak):.3f} m³/s "
            f"超过 {self.total_trigger_m3s:g} m³/s"
        )
        trigger_type = (
            "rolling_step"
            if rolling and self.completed_version
            else "forecast_window_peak"
        )
        return [self._request_forecast(
            observation,
            window,
            exceeded[0],
            peak,
            reason,
            trigger_type,
        )]

    def mark_forecast_started(self, forecast_input_id: str) -> bool:
        if not self._matches_latest_input(forecast_input_id) or self.state != self.PENDING:
            return False
        if self.request_running:
            return False
        self.request_running = True
        return True

    def mark_forecast_completed(self, forecast_input_id: str) -> bool:
        if not self._matches_latest_input(forecast_input_id) or self.state != self.PENDING:
            return False
        self.request_running = False
        self.state = self.ACTIVE
        self.completed_version = self.version
        return True

    def mark_forecast_failed(self, forecast_input_id: str) -> bool:
        if not self._matches_latest_input(forecast_input_id) or self.state != self.PENDING:
            return False
        self.request_running = False
        self.state = self.ACTIVE if self.completed_version else self.NORMAL
        return True

    def _window_from(self, observation: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            sequence = int(observation["sequence"])
        except (KeyError, TypeError, ValueError):
            return []
        if sequence < 0 or sequence >= len(self.reference_rows):
            return []
        window = self.reference_rows[sequence:sequence + FORECAST_WINDOW_POINT_COUNT]
        if not window or window[0].get("observed_at") != observation.get("observed_at"):
            return []
        return window

    def _request_forecast(
        self,
        observation: dict[str, Any],
        window: list[dict[str, Any]],
        first_exceeded: dict[str, Any],
        peak: dict[str, Any],
        reason: str,
        trigger_type: str,
    ) -> dict[str, Any]:
        self.version += 1
        self.state = self.PENDING
        self.request_running = False
        snapshot = self._build_forecast_input(
            observation, window, first_exceeded, peak, reason, trigger_type,
        )
        self.latest_forecast_input = snapshot
        self._write_forecast_input(snapshot)
        input_id = snapshot["summary"]["boundary_flow_id"]
        severity = "critical" if _total_flow(peak) >= self.total_trigger_m3s * 3 else "warning"
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        return {
            "type": "domain_event",
            "event_id": event_id,
            "event_type": "FloodForecastRequired",
            "source_type": "FloodForecastPolicy",
            "source_id": input_id,
            "time": observation["observed_at"],
            "severity": severity,
            "title": (
                "步进触发滚动洪水预测"
                if trigger_type == "rolling_step"
                else "24小时边界流量预测触发洪水预测"
            ),
            "payload": {
                "observation": observation,
                "forecast_input": snapshot["summary"],
                "forecast_trigger": snapshot["forecast_trigger"],
            },
            "correlation_id": self.episode_id,
        }

    def _build_forecast_input(
        self,
        observation: dict[str, Any],
        selected: list[dict[str, Any]],
        first_exceeded: dict[str, Any],
        peak: dict[str, Any],
        reason: str,
        trigger_type: str,
    ) -> dict[str, Any]:
        if len(selected) != FORECAST_WINDOW_POINT_COUNT:
            raise ValueError(
                f"CNN forecast window requires {FORECAST_WINDOW_POINT_COUNT} hourly rows, "
                f"got {len(selected)}"
            )
        window_start = _observed_datetime(selected[0])
        window_end = _observed_datetime(selected[-1])
        if window_end - window_start != timedelta(hours=FORECAST_WINDOW_HOURS):
            raise ValueError("CNN forecast window must span exactly 24 hours")
        input_id = f"boundary_flow_{self.episode_id}_v{self.version:03d}"
        boundaries: dict[str, dict[str, Any]] = {}
        for key, label in BOUNDARIES.items():
            series = []
            for row in selected:
                value = float((row.get("boundaries", {}).get(key) or {}).get("flow_m3s") or 0)
                series.append({
                    "time_h": round((_observed_datetime(row) - window_start).total_seconds() / 3600, 3),
                    "flow_m3s": round(value, 6),
                    "source": "csv_forecast",
                })
            values = [point["flow_m3s"] for point in series]
            boundaries[key] = {
                "label": label,
                "point_count": len(series),
                "series": series,
                "peak_flow_m3s": round(max(values), 3),
                "mean_flow_m3s": round(sum(values) / len(values), 3),
                "first_flow_m3s": round(values[0], 3),
                "last_flow_m3s": round(values[-1], 3),
            }
        rainfall_total = sum(float(row.get("rainfall_mm") or 0) for row in selected)
        summary = {
            "boundary_flow_id": input_id,
            "episode_id": self.episode_id,
            "version": self.version,
            "mode": "csv_playback_forecast",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "triggered_at": observation["observed_at"],
            "simulation_time": observation["observed_at"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "forecast_point_count": len(selected),
            "predicted_rainfall_24h_mm": round(rainfall_total, 3),
            "rainfall_total_mm": round(rainfall_total, 3),
            "forecast_horizon_h": FORECAST_WINDOW_HOURS,
            "reservoir_level_m": float(observation.get("reservoir_level_m") or 0),
            "boundaries": boundaries,
        }
        trigger = {
            "should_run_forecast": True,
            "decision": "request_forecast",
            "trigger_type": trigger_type,
            "reason": reason,
            "policy_state": self.PENDING,
            "current_total_flow_m3s": round(_total_flow(observation), 3),
            "window_peak_total_flow_m3s": round(_total_flow(peak), 3),
            "threshold_exceeded_at": first_exceeded["observed_at"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "threshold_m3s": self.total_trigger_m3s,
            "version": self.version,
        }
        return {"boundary_flow_id": input_id, "summary": summary, "forecast_trigger": trigger}

    def _write_forecast_input(self, snapshot: dict[str, Any]) -> None:
        episode_dir = self.forecast_input_dir / self.episode_id
        version_path = episode_dir / f"v{self.version:03d}.json"
        snapshot["summary"]["input_path"] = rel(version_path)
        episode_dir.mkdir(parents=True, exist_ok=True)
        _write_json(version_path, snapshot)
        self.latest_forecast_input_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.latest_forecast_input_path, snapshot)

    def _matches_latest_input(self, forecast_input_id: str) -> bool:
        current_id = str(((self.latest_forecast_input or {}).get("summary") or {}).get("boundary_flow_id") or "")
        return bool(current_id and current_id == forecast_input_id)


class BoundaryFlowPlayback:
    """Thread-safe composition of the playback source and forecast policy."""

    def __init__(self, source: BoundaryFlowPlaybackSource | None = None,
                 policy: FloodForecastPolicy | None = None):
        self.source = source or BoundaryFlowPlaybackSource()
        self.policy = policy or FloodForecastPolicy(self.source.rows)
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self.source.reset()
            self.policy.reset()

    def next_events(
        self,
        *,
        rolling: bool = False,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self._lock:
            observation = self.source.next_observation()
            if observation is None:
                return None, []
            event = make_boundary_flow_forecast_advanced_event(observation)
            return event, self.policy.observe(observation, rolling=rolling)

    def mark_forecast_started(self, forecast_input_id: str) -> bool:
        with self._lock:
            return self.policy.mark_forecast_started(forecast_input_id)

    def mark_forecast_completed(self, forecast_input_id: str) -> bool:
        with self._lock:
            return self.policy.mark_forecast_completed(forecast_input_id)

    def mark_forecast_failed(self, forecast_input_id: str) -> bool:
        with self._lock:
            return self.policy.mark_forecast_failed(forecast_input_id)

    def status(self) -> dict[str, Any]:
        with self._lock:
            latest = self.policy.last_observation or {}
            return {
                "policy_state": self.policy.state,
                "forecast_version": self.policy.version,
                "completed_forecast_version": self.policy.completed_version,
                "forecast_running": self.policy.request_running,
                "observed_at": latest.get("observed_at"),
                "simulation_time": latest.get("simulation_time") or latest.get("observed_at"),
                "sequence": latest.get("sequence"),
                "total_rows": len(self.source.rows),
                "has_next": self.source.index < len(self.source.rows),
            }


def make_boundary_flow_forecast_advanced_event(observation: dict[str, Any]) -> dict[str, Any]:
    playback_id = str(observation.get("playback_id") or "boundary_playback")
    sequence = int(observation.get("sequence") or 0)
    return {
        "type": "domain_event",
        "event_id": f"forecast_{playback_id}_{sequence:04d}",
        "event_type": "BoundaryFlowForecastAdvanced",
        "source_type": "HydrodynamicBoundary",
        "source_id": playback_id,
        "time": observation["observed_at"],
        "severity": "forecast",
        "title": "边界流量预测时刻更新",
        "payload": {"observation": observation},
        "correlation_id": playback_id,
    }


def read_latest_forecast_input(path: Path | None = None) -> dict[str, Any] | None:
    target = path or latest_forecast_input_path()
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _boundary(key: str, value: Any) -> dict[str, Any]:
    return {"label": BOUNDARIES[key], "flow_m3s": round(_number(value), 6)}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_boundary_flow_time(value: str) -> datetime:
    text = str(value or "").strip()
    for time_format in BOUNDARY_FLOW_TIME_FORMATS:
        try:
            return datetime.strptime(text, time_format)
        except ValueError:
            continue
    raise ValueError(
        "time_period_end 格式应为 YYYY-MM-DD HH:MM 或 YYYY/M/D H:MM"
    )


def _observed_datetime(observation: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(observation["observed_at"]))


def _total_flow(observation: dict[str, Any] | None) -> float:
    if not observation:
        return 0.0
    boundaries = observation.get("boundaries") or {}
    if boundaries:
        return sum(float(item.get("flow_m3s") or 0) for item in boundaries.values())
    return float(observation.get("total_flow_m3s") or 0)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
