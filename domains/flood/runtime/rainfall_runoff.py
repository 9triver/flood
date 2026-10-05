"""演示级降雨—产流计算。

This module intentionally uses a small lumped rainfall-runoff model for
demonstrations. It converts basin rainfall into a reservoir inflow series;
reservoir release and water-level routing remain separate responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .rainfall_input import BASIN_AREAS_KM2

SECONDS_PER_HOUR = 3600.0
MM_KM2_TO_M3 = 1000.0


class RainfallRunoffInputError(ValueError):
    """Raised when the simplified rainfall-runoff input is invalid."""


@dataclass(frozen=True)
class RainfallRunoffParameters:
    """Parameters for the demonstration rainfall-to-inflow mapping.

    ``runoff_coefficient`` is the fraction of rainfall converted to direct
    runoff. ``routing_alpha`` controls a one-step linear reservoir: values
    closer to one respond faster, while lower values smooth the inflow peak.
    ``lag_hours`` delays direct runoff by an integer number of input periods.
    """

    area_km2: float = BASIN_AREAS_KM2["reservoir"]
    runoff_coefficient: float = 0.50
    baseflow_m3s: float = 0.2
    routing_alpha: float = 0.6
    lag_hours: int = 1
    dt_hours: float = 0.5

    def validate(self) -> None:
        if self.area_km2 <= 0:
            raise RainfallRunoffInputError("area_km2 must be positive")
        if not 0 <= self.runoff_coefficient <= 1:
            raise RainfallRunoffInputError(
                "runoff_coefficient must be between 0 and 1"
            )
        if self.baseflow_m3s < 0:
            raise RainfallRunoffInputError("baseflow_m3s cannot be negative")
        if not 0 < self.routing_alpha <= 1:
            raise RainfallRunoffInputError(
                "routing_alpha must be greater than 0 and no greater than 1"
            )
        if int(self.lag_hours) != self.lag_hours or self.lag_hours < 0:
            raise RainfallRunoffInputError(
                "lag_hours must be a non-negative integer"
            )
        if self.dt_hours <= 0:
            raise RainfallRunoffInputError("dt_hours must be positive")


def simulate_rainfall_runoff(
    rainfall_series: Iterable[dict[str, Any] | float | int],
    *,
    area_km2: float = BASIN_AREAS_KM2["reservoir"],
    runoff_coefficient: float = 0.50,
    baseflow_m3s: float = 0.2,
    routing_alpha: float = 0.6,
    lag_hours: int = 1,
    dt_hours: float = 0.5,
) -> dict[str, Any]:
    """Convert a rainfall series into a demonstration reservoir inflow series.

    Each input item can be a numeric rainfall value or a mapping containing
    ``rainfall_mm`` and an optional ``valid_time``/``time`` field. Rainfall is
    interpreted as the accumulated depth during one ``dt_hours`` period.

    The direct-runoff conversion is water balanced at the basin scale:

    ``Q = rainfall_mm * runoff_coefficient * area_km2 / (3.6 * dt_hours)``

    A lag and one-step linear reservoir are applied to direct runoff before
    adding the constant baseflow. This is suitable for UI demonstrations and
    deterministic tests; it is not a calibrated hydrological forecast.
    """

    parameters = RainfallRunoffParameters(
        area_km2=float(area_km2),
        runoff_coefficient=float(runoff_coefficient),
        baseflow_m3s=float(baseflow_m3s),
        routing_alpha=float(routing_alpha),
        lag_hours=int(lag_hours),
        dt_hours=float(dt_hours),
    )
    parameters.validate()
    inputs = [_normalize_rainfall_point(item) for item in rainfall_series]
    if not inputs:
        return {
            "status": "completed",
            "parameters": _parameters_dict(parameters),
            "series": [],
            "peak_inflow_m3s": parameters.baseflow_m3s,
            "total_runoff_depth_mm": 0.0,
        }

    direct_values = [
        rainfall_mm * parameters.runoff_coefficient * parameters.area_km2
        / (3.6 * parameters.dt_hours)
        for rainfall_mm, _ in inputs
    ]
    routed_values: list[float] = []
    previous = 0.0
    for index in range(len(inputs)):
        source_index = index - parameters.lag_hours
        delayed = direct_values[source_index] if source_index >= 0 else 0.0
        routed = parameters.routing_alpha * delayed + (
            1.0 - parameters.routing_alpha
        ) * previous
        routed_values.append(routed)
        previous = routed

    series = []
    for index, ((rainfall_mm, valid_time), direct, routed) in enumerate(
        zip(inputs, direct_values, routed_values)
    ):
        series.append({
            "index": index,
            "valid_time": valid_time,
            "rainfall_mm": round(rainfall_mm, 6),
            "runoff_depth_mm": round(
                rainfall_mm * parameters.runoff_coefficient, 6,
            ),
            "direct_runoff_m3s": round(direct, 6),
            "routed_runoff_m3s": round(routed, 6),
            "reservoir_inflow_m3s": round(
                parameters.baseflow_m3s + routed, 6,
            ),
        })
    return {
        "status": "completed",
        "parameters": _parameters_dict(parameters),
        "series": series,
        "peak_inflow_m3s": max(
            float(point["reservoir_inflow_m3s"]) for point in series
        ),
        "total_runoff_depth_mm": round(
            sum(float(point["runoff_depth_mm"]) for point in series), 6,
        ),
    }


def _normalize_rainfall_point(
    item: dict[str, Any] | float | int,
) -> tuple[float, str | None]:
    if isinstance(item, dict):
        raw_rainfall = item.get("rainfall_mm")
        valid_time = item.get("valid_time") or item.get("time")
    else:
        raw_rainfall = item
        valid_time = None
    try:
        rainfall = float(raw_rainfall)
    except (TypeError, ValueError) as exc:
        raise RainfallRunoffInputError(
            f"rainfall_mm must be numeric: {raw_rainfall!r}"
        ) from exc
    if rainfall < 0:
        raise RainfallRunoffInputError("rainfall_mm cannot be negative")
    return rainfall, str(valid_time) if valid_time is not None else None


def _parameters_dict(parameters: RainfallRunoffParameters) -> dict[str, Any]:
    return {
        "area_km2": parameters.area_km2,
        "runoff_coefficient": parameters.runoff_coefficient,
        "baseflow_m3s": parameters.baseflow_m3s,
        "routing_alpha": parameters.routing_alpha,
        "lag_hours": parameters.lag_hours,
        "dt_hours": parameters.dt_hours,
        "model": "simplified_rainfall_runoff_demo",
    }


__all__ = [
    "RainfallRunoffInputError",
    "RainfallRunoffParameters",
    "simulate_rainfall_runoff",
]
