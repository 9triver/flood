"""Deterministic Longtan reservoir release calculation for demonstrations.

The rule priorities and water-balance equations are adapted from the NHRI
dispatch model under ``temp/Scheduling model``. The runtime version exposes a
small pure function and reads only the versioned curves and limits under
``domains/flood/data/dispatch``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_DIR = Path(__file__).resolve().parents[3]
DISPATCH_DATA_DIR = PROJECT_DIR / "domains" / "flood" / "data" / "dispatch"
VOLUME_FACTOR = 10_000.0
LEVEL_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Curve:
    x: tuple[float, ...]
    y: tuple[float, ...]

    def value(self, value: float) -> float:
        if value <= self.x[0]:
            index = 0
        elif value >= self.x[-1]:
            index = len(self.x) - 2
        else:
            lo, hi = 0, len(self.x) - 1
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if self.x[mid] <= value:
                    lo = mid
                else:
                    hi = mid
            index = lo
        x0, x1 = self.x[index], self.x[index + 1]
        y0, y1 = self.y[index], self.y[index + 1]
        return y0 + (value - x0) * (y1 - y0) / (x1 - x0)


@dataclass(frozen=True)
class DispatchParameters:
    flood_limit_level_m: float = 245.30
    design_flood_level_m: float = 247.92
    check_flood_level_m: float = 248.91
    downstream_safe_release_m3s: float = 30.0
    check_flood_peak_inflow_m3s: float = 679.0
    initial_level_m: float = 245.10
    normal_release_m3s: float = 0.0
    forecast_steps: int = 25


def simulate_reservoir_dispatch(
    inflow_series: Iterable[dict[str, Any] | float | int],
    *,
    parameters: DispatchParameters | None = None,
    initial_level_m: float | None = None,
    dt_hours: float = 1.0,
) -> dict[str, Any]:
    """Convert reservoir inflow into release and end-level series.

    The input is an ordered sequence of ``inflow_m3s`` values. Each period
    uses the remaining forecast window to choose a rule state, then applies
    the storage balance ``V_next = V + (I - Q) * dt / 10000``.
    """

    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    params = parameters or DispatchParameters()
    rows = [_normalize_inflow(item) for item in inflow_series]
    if not rows:
        return {"status": "completed", "series": [], "peak_inflow_m3s": 0.0, "max_level_m": params.initial_level_m}
    storage_curve = _load_curve("storage_capacity_curve.dat")
    outflow_curve = _load_curve("outflow_curve.dat")
    initial_level = params.initial_level_m if initial_level_m is None else float(initial_level_m)
    storage = storage_curve.value(initial_level)
    limit_storage = storage_curve.value(params.flood_limit_level_m)
    output = []
    peak_inflow = max(item["inflow_m3s"] for item in rows)
    super_standard = peak_inflow > params.check_flood_peak_inflow_m3s + LEVEL_TOLERANCE
    step_seconds = dt_hours * 3600.0

    for index, item in enumerate(rows):
        level = _inverse_curve(storage_curve, storage)
        capacity = max(0.0, outflow_curve.value(level)) if level > params.flood_limit_level_m else 0.0
        window = rows[index:index + max(1, params.forecast_steps)]
        inflows = [row["inflow_m3s"] for row in window]
        max_full = _project_max_level(storage, inflows, step_seconds, storage_curve, outflow_curve, "full", params)
        max_safe = _project_max_level(storage, inflows, step_seconds, storage_curve, outflow_curve, "safe", params)
        next_safe = _project_next_level(storage, inflows[0], step_seconds, storage_curve, outflow_curve, params)
        required = _required_release(storage, limit_storage, inflows, step_seconds)
        emergency = super_standard or max_full > params.check_flood_level_m + LEVEL_TOLERANCE
        rising_above_limit = level >= params.flood_limit_level_m - LEVEL_TOLERANCE and next_safe > level + LEVEL_TOLERANCE
        imminent_limit = level < params.flood_limit_level_m - LEVEL_TOLERANCE and next_safe > params.flood_limit_level_m + LEVEL_TOLERANCE
        if emergency:
            state, release = "EMERGENCY", capacity
        elif rising_above_limit or imminent_limit:
            state, release = "FULL_CAPACITY", capacity
        elif level > params.flood_limit_level_m + LEVEL_TOLERANCE:
            state, release = "DRAWDOWN", min(capacity, params.downstream_safe_release_m3s)
        elif _project_max_level(storage, inflows, step_seconds, storage_curve, outflow_curve, "base", params) > params.flood_limit_level_m + LEVEL_TOLERANCE:
            state, release = "PRERELEASE", min(required, max(inflows), params.downstream_safe_release_m3s, capacity)
        else:
            state, release = "NORMAL", min(params.normal_release_m3s, params.downstream_safe_release_m3s, capacity)
        water_limited = inflows[0] + max(0.0, storage - limit_storage) * VOLUME_FACTOR / step_seconds
        release = max(0.0, min(release, water_limited))
        end_storage = storage + (inflows[0] - release) * step_seconds / VOLUME_FACTOR
        end_level = _inverse_curve(storage_curve, end_storage)
        output.append({
            "index": index,
            "valid_time": item["valid_time"],
            "inflow_m3s": round(inflows[0], 6),
            "release_m3s": round(release, 6),
            "start_level_m": round(level, 6),
            "end_level_m": round(end_level, 6),
            "state": state,
            "forecast_peak_inflow_m3s": round(max(inflows), 6),
            "forecast_max_level_safe_m": round(max_safe, 6),
            "forecast_max_level_full_m": round(max_full, 6),
            "required_release_m3s": round(required, 6),
            "available_release_m3s": round(capacity, 6),
        })
        storage = end_storage
    return {
        "status": "completed",
        "parameters": params.__dict__,
        "series": output,
        "peak_inflow_m3s": round(peak_inflow, 6),
        "max_level_m": round(max(item["end_level_m"] for item in output), 6),
        "super_standard_flood": super_standard,
    }


def _load_curve(filename: str) -> Curve:
    rows = []
    for line in (DISPATCH_DATA_DIR / filename).read_text(encoding="utf-8-sig").splitlines():
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        parts = clean.replace(",", " ").split()
        if len(parts) >= 3 and parts[0].isdigit():
            rows.append((float(parts[1]), float(parts[2])))
    if len(rows) < 2:
        raise ValueError(f"dispatch curve has fewer than two points: {filename}")
    return Curve(tuple(row[0] for row in rows), tuple(row[1] for row in rows))


def _inverse_curve(curve: Curve, value: float) -> float:
    return Curve(curve.y, curve.x).value(value)


def _project_max_level(storage: float, inflows: list[float], step_seconds: float,
                       storage_curve: Curve, outflow_curve: Curve,
                       mode: str, params: DispatchParameters) -> float:
    current = storage
    maximum = _inverse_curve(storage_curve, current)
    for inflow in inflows:
        level = _inverse_curve(storage_curve, current)
        capacity = max(0.0, outflow_curve.value(level)) if level > params.flood_limit_level_m else 0.0
        release = capacity if mode == "full" else min(capacity, params.downstream_safe_release_m3s) if mode == "safe" else min(capacity, params.normal_release_m3s)
        release = min(release, inflow + max(0.0, current - storage_curve.value(params.flood_limit_level_m)) * VOLUME_FACTOR / step_seconds)
        current += (inflow - release) * step_seconds / VOLUME_FACTOR
        maximum = max(maximum, _inverse_curve(storage_curve, current))
    return maximum


def _project_next_level(storage: float, inflow: float, step_seconds: float,
                        storage_curve: Curve, outflow_curve: Curve,
                        params: DispatchParameters) -> float:
    level = _inverse_curve(storage_curve, storage)
    capacity = max(0.0, outflow_curve.value(level)) if level > params.flood_limit_level_m else 0.0
    release = min(params.downstream_safe_release_m3s, capacity)
    release = min(release, inflow + max(0.0, storage - storage_curve.value(params.flood_limit_level_m)) * VOLUME_FACTOR / step_seconds)
    return _inverse_curve(storage_curve, storage + (inflow - release) * step_seconds / VOLUME_FACTOR)


def _required_release(storage: float, limit_storage: float, inflows: list[float], step_seconds: float) -> float:
    cumulative_volume = (storage - limit_storage) * VOLUME_FACTOR
    cumulative_seconds = 0.0
    required = 0.0
    for inflow in inflows:
        cumulative_volume += inflow * step_seconds
        cumulative_seconds += step_seconds
        required = max(required, cumulative_volume / cumulative_seconds)
    return max(0.0, required)


def _normalize_inflow(item: dict[str, Any] | float | int) -> dict[str, Any]:
    if isinstance(item, dict):
        value = item.get("reservoir_inflow_m3s", item.get("inflow_m3s"))
        valid_time = item.get("valid_time") or item.get("time")
    else:
        value, valid_time = item, None
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("reservoir inflow must be finite and non-negative")
    return {"inflow_m3s": value, "valid_time": valid_time}


__all__ = ["DispatchParameters", "simulate_reservoir_dispatch"]
