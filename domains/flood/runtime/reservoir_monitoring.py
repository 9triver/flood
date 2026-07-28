from __future__ import annotations

from functools import lru_cache
from typing import Any

from oag.ontology.rules import RuleEngine
from oag.ontology.schema import Ontology

from .common import DOMAIN_DIR


LONGTAN_RESERVOIR_ID = "longtan"
LONGTAN_RESERVOIR_STATION_ID = "HP0014511220000128"
RESERVOIR_STATUS_RULE = "reservoir_level_status"
RESERVOIR_ALERT_RULE = "reservoir_level_alert"

THRESHOLD_PROPERTY_NAMES = (
    "normal_pool_level_m",
    "design_flood_level_m",
    "check_flood_level_m",
    "design_flood_warning_level_m",
    "check_flood_warning_level_m",
)


@lru_cache(maxsize=1)
def _ontology() -> Ontology:
    return Ontology.load(DOMAIN_DIR / "ontology.yaml")


@lru_cache(maxsize=1)
def _rule_engine() -> RuleEngine:
    # apply_to_record only uses compiled ontology rules and does not access a repository.
    return RuleEngine(_ontology(), None)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def reservoir_level_thresholds() -> dict[str, float]:
    properties = _ontology().objects["Reservoir"].properties
    thresholds: dict[str, float] = {}
    for name in THRESHOLD_PROPERTY_NAMES:
        value = properties[name].default
        if value is None:
            raise ValueError(f"Reservoir.{name} must define an ontology default")
        thresholds[name] = float(value)
    return thresholds


def assess_reservoir_window(
    current: dict[str, Any],
    forecast: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate the current-to-+24h reservoir window with ontology rules."""
    points = [current, *forecast]
    valid_points = [
        point for point in points
        if _optional_number(point.get("reservoir_level_m")) is not None
    ]
    if not valid_points:
        return {
            "reservoir_id": LONGTAN_RESERVOIR_ID,
            "station_id": LONGTAN_RESERVOIR_STATION_ID,
            "window_hours": len(forecast),
            "thresholds": reservoir_level_thresholds(),
            "current": None,
            "peak": None,
            "alert": None,
        }

    peak = max(
        valid_points,
        key=lambda point: float(point["reservoir_level_m"]),
    )
    current_level = float(current["reservoir_level_m"])
    peak_level = float(peak["reservoir_level_m"])
    engine = _rule_engine()
    alert = engine.apply_to_record(
        RESERVOIR_ALERT_RULE,
        {"reservoir_level_m": peak_level},
    )
    if alert:
        alert = dict(alert)
        for index, point in enumerate(points):
            level = _optional_number(point.get("reservoir_level_m"))
            if level is None:
                continue
            point_alert = engine.apply_to_record(
                RESERVOIR_ALERT_RULE,
                {"reservoir_level_m": level},
            )
            if point_alert and point_alert.get("severity") == alert.get("severity"):
                alert["triggered_at"] = _point_time(point)
                alert["triggered_in_forecast"] = index > 0
                break
    return {
        "reservoir_id": LONGTAN_RESERVOIR_ID,
        "station_id": LONGTAN_RESERVOIR_STATION_ID,
        "window_hours": len(forecast),
        "thresholds": reservoir_level_thresholds(),
        "current": {
            "level_m": round(current_level, 3),
            "valid_time": _point_time(current),
            "status": reservoir_level_status(current_level),
        },
        "peak": {
            "level_m": round(peak_level, 3),
            "valid_time": _point_time(peak),
            "status": reservoir_level_status(peak_level),
        },
        "alert": alert,
    }


def reservoir_level_status(level_m: float) -> dict[str, Any] | None:
    """Return the ontology-defined display status for one reservoir level."""
    return _rule_engine().apply_to_record(
        RESERVOIR_STATUS_RULE,
        {"reservoir_level_m": float(level_m)},
    )


def _point_time(point: dict[str, Any]) -> str:
    return str(
        point.get("valid_time")
        or point.get("simulation_time")
        or point.get("observed_at")
        or ""
    )


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
