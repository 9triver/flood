"""Application-facing facade for deterministic flood-domain functions.

The individual runtime modules remain focused on their calculations.  This
facade is the composition boundary used by OAG registration, so function
wiring does not need to know the implementation module of every operation.
"""

from __future__ import annotations

from typing import Any

from .forecast_context import forecast_input_context, resolve_forecast_context, get_flood_status, unavailable_forecast
from .nearby import find_nearby_objects
from .evacuation_timing import analyze_latest_evacuation_time
from .forecast import assess_flood_emergency, run_flood_forecast
from .impact_analysis import BRIDGE_INFLUENCE_RADIUS_M, analyze_inundation_impacts
from .route_planning import plan_route


class FloodRuntimeService:
    def __init__(self, resolver: Any):
        self.resolver = resolver

    def find_nearby_objects(self, reference_object_type: str, reference_object_id: str,
                            target_type: str = "EvacuationSite", radius_m: float = 3000,
                            limit: int = 10, filters: dict | None = None,
                            min_distance_m: float = 0, offset: int = 0,
                            exclude_object_ids: list[str] | None = None) -> dict[str, Any]:
        return find_nearby_objects(self.resolver, reference_object_type, reference_object_id,
                                   target_type, radius_m, limit, filters, min_distance_m, offset, exclude_object_ids)

    def get_flood_status(self, view: str = "current", time_h: float | None = None) -> dict[str, Any]:
        return get_flood_status(view, time_h)

    def run_flood_forecast(self, forecast_id: str = "latest",
                           force: bool = False) -> dict[str, Any]:
        context = forecast_input_context()
        if not context["available"]:
            return unavailable_forecast(context)
        if forecast_id not in {"latest", "forecast_latest"}:
            return {"error": "运行预测只接受当前输入；历史预测不能作为当前运行结果。"}
        return run_flood_forecast(self.resolver, forecast_id, force)

    def assess_flood_emergency(self, refresh: bool = False) -> dict[str, Any]:
        context = resolve_forecast_context(view="envelope")
        if not context["available"]:
            return unavailable_forecast(context)
        return assess_flood_emergency(self.resolver, refresh)

    def analyze_inundation_impacts(
        self,
        forecast_id: str = "latest",
        target_type: str = "all",
        min_depth_m: float = 0.15,
        max_distance_m: float = 10,
        time_h: float | None = None,
        bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
        view: str = "current",
        object_ids: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = resolve_forecast_context(forecast_id, time_h, view)
        if not context["available"]:
            return unavailable_forecast(context)
        time_h = context["time_h"]
        result = analyze_inundation_impacts(
            self.resolver,
            forecast_id,
            target_type,
            min_depth_m,
            max_distance_m,
            time_h,
            bridge_influence_radius_m,
            object_ids,
            filters,
        )
        if result.get("status") == "no_forecast_cells":
            # Context validation above established that this is a valid dry result.
            result["status"] = "completed"
            result["basis"] = "对应的有效预测中没有淹没网格，不代表现场实测。"
        return result


    def analyze_latest_evacuation_time(
        self,
        evacuation_unit_id: str = "",
        evacuation_unit_name: str = "",
        evacuation_route_id: str = "",
        forecast_id: str = "latest",
        blocked_depth_m: float = 0.3,
        clearance_duration_min: float | None = None,
        safety_buffer_min: float = 0,
    ) -> dict[str, Any]:
        context = resolve_forecast_context(forecast_id, view="envelope")
        if not context["available"]:
            return unavailable_forecast(context)
        return analyze_latest_evacuation_time(
            self.resolver,
            evacuation_unit_id,
            evacuation_unit_name,
            evacuation_route_id,
            forecast_id,
            blocked_depth_m,
            clearance_duration_min,
            safety_buffer_min,
        )

    def plan_route(
        self,
        start_object_type: str = "EvacuationUnit",
        start_object_id: str = "",
        destination_site_id: str = "",
        start_lon: float | str | None = None,
        start_lat: float | str | None = None,
        destination_lon: float | str | None = None,
        destination_lat: float | str | None = None,
        forecast_id: str = "latest",
        time_h: float | str | None = None,
        blocked_depth_m: float | str | None = None,
        profile: str = "car",
        max_endpoint_distance_m: float = 800,
        max_detour_ratio: float = 10,
        view: str = "current",
    ) -> dict[str, Any]:
        return plan_route(
            self.resolver,
            start_object_type,
            start_object_id,
            destination_site_id,
            start_lon,
            start_lat,
            destination_lon,
            destination_lat,
            forecast_id,
            time_h,
            blocked_depth_m,
            profile,
            max_endpoint_distance_m,
            max_detour_ratio,
            view,
        )


__all__ = ["FloodRuntimeService"]
