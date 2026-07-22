from __future__ import annotations

import csv
import json
import math
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

BASEFLOW_EPISODE_FACTOR = 1.20

DRY_FLOW_THRESHOLDS_M3S = {
    "interval1": 2.9,
    "interval2": 0.4,
    "tonggu": 5.4,
    "upstream": 3.6,
}

CLEAR_FLOW_THRESHOLDS_M3S = {
    key: round(value * 0.6, 3)
    for key, value in DRY_FLOW_THRESHOLDS_M3S.items()
}

DEFAULT_BOUNDARY_FLOW_CSV_PATH = DOMAIN_DATA_DIR / "mock" / "boundary_flow.csv"
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


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
            observed_at = datetime.strptime(
                str(raw.get("time_period_end") or ""),
                "%Y-%m-%d %H:%M",
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
    """Turns boundary observations into forecast lifecycle domain events."""

    NORMAL = "NORMAL"
    RISING = "RISING"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    RECEDING = "RECEDING"
    CLOSED = "CLOSED"

    def __init__(self, reference_rows: list[dict[str, Any]], *,
                 forecast_input_dir: Path | None = None,
                 latest_forecast_input_path: Path | None = None,
                 total_trigger_m3s: float = 14.0,
                 deviation_ratio: float = 0.20,
                 cooldown_hours: float = 3.0):
        self.reference_rows = reference_rows
        self.forecast_input_dir = forecast_input_dir or globals()["forecast_input_dir"]()
        self.latest_forecast_input_path = (
            latest_forecast_input_path or globals()["latest_forecast_input_path"]()
        )
        self._workspace_forecast_paths = (
            forecast_input_dir is None and latest_forecast_input_path is None
        )
        self.total_trigger_m3s = total_trigger_m3s
        self.deviation_ratio = deviation_ratio
        self.cooldown_hours = cooldown_hours
        self.reset()

    def reset(self) -> None:
        if self._workspace_forecast_paths:
            self.forecast_input_dir = forecast_input_dir()
            self.latest_forecast_input_path = latest_forecast_input_path()
        self.state = self.NORMAL
        self.episode_id = ""
        self.episode_started_at: datetime | None = None
        self.last_observation: dict[str, Any] | None = None
        self.last_request_at: datetime | None = None
        self.latest_forecast_input: dict[str, Any] | None = None
        self.version = 0
        self.rising_periods = 0
        self.deviation_periods = 0
        self.clear_periods = 0
        self.request_running = False
        self.observations: dict[str, dict[str, Any]] = {}

    def observe(self, observation: dict[str, Any]) -> list[dict[str, Any]]:
        observed_at = _observed_datetime(observation)
        self.observations[observation["observed_at"]] = observation
        previous_total = _total_flow(self.last_observation)
        current_total = _total_flow(observation)
        rising = self.last_observation is not None and current_total > previous_total + 0.01
        clear_now = self._is_clear(observation)
        self.rising_periods = self.rising_periods + 1 if rising else 0

        if self.episode_started_at is None and rising and _is_above_baseflow(observation):
            self.episode_started_at = observed_at
            self.episode_id = f"flood_{observed_at.strftime('%Y%m%dT%H%M')}"
        if self.state == self.NORMAL and self.episode_started_at is not None:
            self.state = self.RISING

        events: list[dict[str, Any]] = []
        if self.state == self.RISING and self._initial_trigger_matches(observation):
            event = self._request_forecast(observation, "initial", "边界总流量达到门槛且已连续两个时段上涨")
            if event:
                events.append(event)
        elif self.state in {self.ACTIVE, self.RECEDING} and not clear_now:
            if self.state == self.ACTIVE and current_total < previous_total - 0.01:
                self.state = self.RECEDING
            deviation = self._forecast_deviation(observation)
            self.deviation_periods = self.deviation_periods + 1 if deviation > self.deviation_ratio else 0
            renewed_rise = self.state == self.RECEDING and rising
            if self._cooldown_elapsed(observed_at):
                if self.deviation_periods >= 2:
                    event = self._request_forecast(
                        observation,
                        "deviation",
                        f"实测边界流量连续两个时段偏离当前预测超过 {self.deviation_ratio:.0%}",
                    )
                    if event:
                        events.append(event)
                elif renewed_rise:
                    event = self._request_forecast(observation, "renewed_rise", "退水阶段边界流量再次上涨")
                    if event:
                        events.append(event)

        if self.state in {self.PENDING, self.ACTIVE, self.RECEDING}:
            if clear_now:
                self.clear_periods += 1
            else:
                self.clear_periods = 0
            if self.clear_periods >= 3:
                self.state = self.CLOSED
                self.request_running = False
                events.append(self._episode_ended_event(observation))

        self.last_observation = observation
        return events

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
        self.deviation_periods = 0
        return True

    def mark_forecast_failed(self, forecast_input_id: str) -> bool:
        if not self._matches_latest_input(forecast_input_id) or self.state != self.PENDING:
            return False
        self.request_running = False
        self.state = self.RISING
        return True

    def request_window_revision(self, reason: str = "预测窗口被显式修订") -> dict[str, Any] | None:
        if not self.last_observation or self.state not in {self.ACTIVE, self.RECEDING}:
            return None
        if not self._cooldown_elapsed(_observed_datetime(self.last_observation)):
            return None
        return self._request_forecast(self.last_observation, "window_revision", reason)

    def _initial_trigger_matches(self, observation: dict[str, Any]) -> bool:
        boundaries = observation.get("boundaries") or {}
        exceeded = any(
            float((boundaries.get(key) or {}).get("flow_m3s") or 0) >= threshold
            for key, threshold in DRY_FLOW_THRESHOLDS_M3S.items()
        )
        return exceeded and _total_flow(observation) >= self.total_trigger_m3s and self.rising_periods >= 2

    def _request_forecast(self, observation: dict[str, Any], trigger_type: str,
                          reason: str) -> dict[str, Any] | None:
        if self.state == self.PENDING:
            return None
        self.version += 1
        self.last_request_at = _observed_datetime(observation)
        self.state = self.PENDING
        self.request_running = False
        self.deviation_periods = 0
        snapshot = self._build_forecast_input(observation, trigger_type, reason)
        self.latest_forecast_input = snapshot
        self._write_forecast_input(snapshot)
        input_id = snapshot["summary"]["boundary_flow_id"]
        severity = "critical" if _total_flow(observation) >= self.total_trigger_m3s * 3 else "warning"
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        return {
            "type": "domain_event",
            "event_id": event_id,
            "event_type": "FloodForecastRequired",
            "source_type": "FloodForecastPolicy",
            "source_id": input_id,
            "time": observation["observed_at"],
            "severity": severity,
            "title": "边界流量触发洪水预测" if self.version == 1 else "边界流量触发洪水重算",
            "payload": {
                "observation": observation,
                "forecast_input": snapshot["summary"],
                "forecast_trigger": snapshot["forecast_trigger"],
            },
            "correlation_id": self.episode_id,
        }

    def _build_forecast_input(self, observation: dict[str, Any],
                              trigger_type: str, reason: str) -> dict[str, Any]:
        if self.episode_started_at is None:
            raise RuntimeError("forecast input requires an active flood episode")
        window_start = self.episode_started_at
        window_end = window_start + timedelta(hours=24)
        selected = [
            row for row in self.reference_rows
            if window_start <= _observed_datetime(row) <= window_end
        ]
        if len(selected) != 25:
            raise ValueError(
                f"CNN forecast window requires 25 hourly rows, got {len(selected)} "
                f"from {window_start.isoformat()} to {window_end.isoformat()}"
            )
        observed_through = _observed_datetime(observation)
        input_id = f"boundary_flow_{self.episode_id}_v{self.version:03d}"
        boundaries: dict[str, dict[str, Any]] = {}
        for key, label in BOUNDARIES.items():
            series = []
            for row in selected:
                source = self.observations.get(row["observed_at"], row)
                value = float((source.get("boundaries", {}).get(key) or {}).get("flow_m3s") or 0)
                series.append({
                    "time_h": round((_observed_datetime(row) - window_start).total_seconds() / 3600, 3),
                    "flow_m3s": round(value, 6),
                    "source": "observed" if _observed_datetime(row) <= observed_through else "mock_forecast",
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
                "rising_ratio": round(max(values) / max(values[0], 0.01), 3),
            }
        rain_rows = [
            self.observations.get(row["observed_at"], row)
            for row in selected
        ]
        observed_rainfall = sum(
            float(row.get("rainfall_mm") or 0)
            for row in rain_rows
            if _observed_datetime(row) <= observed_through
        )
        forecast_rainfall = sum(
            float(row.get("rainfall_mm") or 0)
            for row in rain_rows
            if _observed_datetime(row) > observed_through
        )
        summary = {
            "boundary_flow_id": input_id,
            "episode_id": self.episode_id,
            "version": self.version,
            "mode": "csv_playback_forecast",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "triggered_at": observation["observed_at"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "observed_through": observation["observed_at"],
            "observed_point_count": sum(
                1 for row in selected if _observed_datetime(row) <= observed_through
            ),
            "forecast_point_count": sum(
                1 for row in selected if _observed_datetime(row) > observed_through
            ),
            "observed_rainfall_mm": round(observed_rainfall, 3),
            "forecast_rainfall_mm": round(forecast_rainfall, 3),
            "rainfall_total_mm": round(observed_rainfall + forecast_rainfall, 3),
            "forecast_horizon_h": round(max(0.0, (window_end - observed_through).total_seconds() / 3600), 3),
            "reservoir_level_m": float(observation.get("reservoir_level_m") or 0),
            "boundaries": boundaries,
            "flow_index": _flow_index(boundaries),
        }
        trigger = {
            "should_run_forecast": True,
            "decision": "request_forecast",
            "trigger_type": trigger_type,
            "reason": reason,
            "policy_state": self.PENDING,
            "total_flow_m3s": round(_total_flow(observation), 3),
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

    def _forecast_deviation(self, observation: dict[str, Any]) -> float:
        if not self.latest_forecast_input:
            return 0.0
        summary = self.latest_forecast_input.get("summary") or {}
        start_text = str(summary.get("window_start") or "")
        if not start_text:
            return 0.0
        time_h = (_observed_datetime(observation) - datetime.fromisoformat(start_text)).total_seconds() / 3600
        ratios = []
        for key in BOUNDARIES:
            series = ((summary.get("boundaries") or {}).get(key) or {}).get("series") or []
            expected = next(
                (float(point.get("flow_m3s") or 0) for point in series
                 if math.isclose(float(point.get("time_h") or 0), time_h, abs_tol=1e-6)),
                None,
            )
            if expected is None:
                continue
            actual = float(((observation.get("boundaries") or {}).get(key) or {}).get("flow_m3s") or 0)
            denominator = max(abs(expected), DRY_FLOW_THRESHOLDS_M3S[key], 0.1)
            ratios.append(abs(actual - expected) / denominator)
        return max(ratios, default=0.0)

    def _cooldown_elapsed(self, observed_at: datetime) -> bool:
        if self.last_request_at is None:
            return True
        return (observed_at - self.last_request_at).total_seconds() >= self.cooldown_hours * 3600

    def _is_clear(self, observation: dict[str, Any]) -> bool:
        if float(observation.get("rainfall_mm") or 0) > 0:
            return False
        boundaries = observation.get("boundaries") or {}
        return all(
            float((boundaries.get(key) or {}).get("flow_m3s") or 0) < threshold
            for key, threshold in CLEAR_FLOW_THRESHOLDS_M3S.items()
        )

    def _matches_latest_input(self, forecast_input_id: str) -> bool:
        current_id = str(((self.latest_forecast_input or {}).get("summary") or {}).get("boundary_flow_id") or "")
        return bool(current_id and current_id == forecast_input_id)

    def _episode_ended_event(self, observation: dict[str, Any]) -> dict[str, Any]:
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        return {
            "type": "domain_event",
            "event_id": event_id,
            "event_type": "FloodEpisodeEnded",
            "source_type": "FloodForecastPolicy",
            "source_id": self.episode_id,
            "time": observation["observed_at"],
            "severity": "normal",
            "title": "洪水过程结束",
            "payload": {
                "episode_id": self.episode_id,
                "ended_at": observation["observed_at"],
                "clear_periods": self.clear_periods,
                "forecast_versions": self.version,
            },
            "correlation_id": self.episode_id,
        }


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

    def next_events(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        with self._lock:
            observation = self.source.next_observation()
            if observation is None:
                return None, []
            event = make_boundary_flow_observed_event(observation)
            return event, self.policy.observe(observation)

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
                "observed_at": latest.get("observed_at"),
                "sequence": latest.get("sequence"),
                "total_rows": len(self.source.rows),
            }


def make_boundary_flow_observed_event(observation: dict[str, Any]) -> dict[str, Any]:
    playback_id = str(observation.get("playback_id") or "boundary_playback")
    sequence = int(observation.get("sequence") or 0)
    return {
        "type": "domain_event",
        "event_id": f"obs_{playback_id}_{sequence:04d}",
        "event_type": "BoundaryFlowObserved",
        "source_type": "HydrodynamicBoundary",
        "source_id": playback_id,
        "time": observation["observed_at"],
        "severity": "observation",
        "title": "边界流量观测更新",
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


def _observed_datetime(observation: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(observation["observed_at"]))


def _total_flow(observation: dict[str, Any] | None) -> float:
    if not observation:
        return 0.0
    if "total_flow_m3s" in observation:
        return float(observation.get("total_flow_m3s") or 0)
    return sum(
        float(item.get("flow_m3s") or 0)
        for item in (observation.get("boundaries") or {}).values()
    )


def _is_above_baseflow(observation: dict[str, Any]) -> bool:
    boundaries = observation.get("boundaries") or {}
    return any(
        float((boundaries.get(key) or {}).get("flow_m3s") or 0) > baseflow * BASEFLOW_EPISODE_FACTOR
        for key, baseflow in BASE_FLOWS_M3S.items()
    )


def _flow_index(boundaries: dict[str, dict[str, Any]]) -> float:
    ratios = []
    for key, threshold in DRY_FLOW_THRESHOLDS_M3S.items():
        peak = float((boundaries.get(key) or {}).get("peak_flow_m3s") or 0)
        ratios.append(peak / max(threshold, 1e-6))
    return round(max(0.0, min(8.0, math.sqrt(sum(ratios) / len(ratios)))), 4) if ratios else 0.0


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
