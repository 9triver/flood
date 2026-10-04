from __future__ import annotations

import math

from dataclasses import dataclass
from typing import Any

from domains.flood.runtime.forecast_context import resolve_forecast_context
from domains.flood.runtime.hydrodynamic_grid import hydrodynamic_grid_stats
from server.presentation.types import MapAction


@dataclass(frozen=True)
class HydrodynamicActionPlan:
    actions: list[MapAction]
    object_type: str
    filters: dict[str, Any]


def build_hydrodynamic_action_plan(
    object_type: str,
    filters: dict[str, Any],
    *,
    label: str,
    fit: bool,
    refresh: bool,
) -> HydrodynamicActionPlan | None:
    if object_type not in {"HydrodynamicGridCell", "InundationForecastCell"}:
        return None
    if not isinstance(filters, dict) or set(filters) - {"forecast_id", "time_h", "view"}:
        raise ValueError("hydrodynamic filters only support forecast_id, time_h, view")
    view = filters.get("view", "current")
    if view not in {"current", "time_slice", "envelope"}:
        raise ValueError("view must be current, time_slice or envelope")
    if "time_h" in filters:
        hour = filters["time_h"]
        if isinstance(hour, bool) or not isinstance(hour, (int, float)) or not math.isfinite(hour) or hour < 0 or view == "envelope":
            raise ValueError("time_h must be a finite nonnegative hour for time_slice view")
    if is_hydrodynamic_result_request(object_type, filters):
        result_filters = hydrodynamic_result_filters(object_type, filters)
        context = resolve_forecast_context(hydrodynamic_result_id(result_filters), result_filters.get("time_h"), view)
        if not context["available"]:
            raise ValueError(context["reason"])
        result_filters["view"] = "envelope" if view == "envelope" else "time_slice"
        if context["time_h"] is not None:
            result_filters["time_h"] = context["time_h"]
        return HydrodynamicActionPlan(
            actions=[{
                "type": "apply_hydrodynamic_result",
                "filters": result_filters,
                "label": label,
                "fit": fit,
                "refresh": refresh,
            }],
            object_type="HydrodynamicGridCell",
            filters=result_filters,
        )
    if object_type == "HydrodynamicGridCell":
        if filters:
            raise ValueError("forecast_id is required for forecast time/view filters")
        return HydrodynamicActionPlan(
            actions=[{
                "type": "show_hydrodynamic_mesh",
                "fit": fit,
                "mesh_only": False,
                "refresh": refresh,
            }],
            object_type="HydrodynamicGridCell",
            filters={"result": "mesh"},
        )
    return None


def count_hydrodynamic(object_type: str,
                       filters: dict[str, Any]) -> int | None:
    if is_hydrodynamic_result_request(object_type, filters):
        stats = hydrodynamic_grid_stats(hydrodynamic_result_id(filters))
        return int(
            (stats.get("forecast") or {}).get("flooded_count", 0) or 0
        )
    if object_type == "HydrodynamicGridCell":
        stats = hydrodynamic_grid_stats("mesh")
        return int(stats.get("feature_count") or 0)
    return None


def default_hydrodynamic_label(object_type: str,
                               filters: dict[str, Any]) -> str | None:
    if object_type not in {"InundationForecastCell", "HydrodynamicGridCell"}:
        return None
    if object_type == "InundationForecastCell" or filters.get("forecast_id") == "latest":
        return "预测淹没结果"
    forecast_id = filters.get("forecast_id")
    if forecast_id and forecast_id != "latest":
        return f"{forecast_id} 水动力结果"
    return None


def hydrodynamic_result_id(filters: dict[str, Any]) -> str:
    return str(filters.get("forecast_id") or "latest")


def is_hydrodynamic_result_request(object_type: str,
                                   filters: dict[str, Any]) -> bool:
    return object_type == "InundationForecastCell" or (
        object_type == "HydrodynamicGridCell"
        and bool(filters.get("forecast_id"))
    )


def hydrodynamic_result_filters(object_type: str,
                                filters: dict[str, Any]) -> dict[str, Any]:
    if object_type == "InundationForecastCell" and not filters.get("forecast_id"):
        return {**filters, "forecast_id": "latest"}
    return dict(filters)
