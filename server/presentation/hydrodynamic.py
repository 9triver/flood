from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    if is_hydrodynamic_result_request(object_type, filters):
        result_filters = hydrodynamic_result_filters(object_type, filters)
        return HydrodynamicActionPlan(
            actions=[
                {"type": "show_hydrodynamic_mesh", "fit": False},
                {
                    "type": "apply_hydrodynamic_result",
                    "filters": result_filters,
                    "label": label,
                    "fit": False,
                    "refresh": refresh,
                },
            ],
            object_type="HydrodynamicCell",
            filters=result_filters,
        )
    if object_type == "HydrodynamicCell":
        return HydrodynamicActionPlan(
            actions=[{
                "type": "show_hydrodynamic_mesh",
                "fit": fit,
                "mesh_only": True,
            }],
            object_type="HydrodynamicCell",
            filters={"result": "mesh"},
        )
    return None


def count_hydrodynamic(object_type: str,
                       filters: dict[str, Any]) -> int | None:
    if is_hydrodynamic_result_request(object_type, filters):
        stats = hydrodynamic_grid_stats(hydrodynamic_result_id(filters))
        return int(
            (stats.get("forecast") or {}).get("flooded_count")
            or stats.get("feature_count")
            or 0
        )
    if object_type == "HydrodynamicCell":
        stats = hydrodynamic_grid_stats("mesh")
        return int(stats.get("feature_count") or 0)
    return None


def default_hydrodynamic_label(object_type: str,
                               filters: dict[str, Any]) -> str | None:
    if object_type not in {"ForecastCell", "HydrodynamicCell"}:
        return None
    if object_type == "ForecastCell" or filters.get("forecast_id") == "latest":
        return "预测淹没结果"
    forecast_id = filters.get("forecast_id")
    if forecast_id and forecast_id != "latest":
        return f"{forecast_id} 水动力结果"
    return None


def hydrodynamic_result_id(filters: dict[str, Any]) -> str:
    return str(filters.get("forecast_id") or "latest")


def is_hydrodynamic_result_request(object_type: str,
                                   filters: dict[str, Any]) -> bool:
    return object_type == "ForecastCell" or (
        object_type == "HydrodynamicCell"
        and bool(filters.get("forecast_id"))
    )


def hydrodynamic_result_filters(object_type: str,
                                filters: dict[str, Any]) -> dict[str, Any]:
    if object_type == "ForecastCell" and not filters.get("forecast_id"):
        return {**filters, "forecast_id": "latest"}
    return dict(filters)
