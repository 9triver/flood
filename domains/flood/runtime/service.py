"""Application-facing facade for deterministic flood-domain functions.

The individual runtime modules remain focused on their calculations.  This
facade is the composition boundary used by OAG registration, so function
wiring does not need to know the implementation module of every operation.
"""

from __future__ import annotations

from typing import Any

from .forecast_context import forecast_input_context, resolve_forecast_context, get_flood_status, unavailable_forecast
from .object_sets import save_object_set, set_summary, refine_object_set, read_object_set
from .evacuation_options import compare_evacuation_sites, review_route
from .nearby import find_nearby_objects
from .evacuation_timing import analyze_latest_evacuation_time
from .forecast import assess_flood_emergency, run_flood_forecast
from .impact_analysis import BRIDGE_INFLUENCE_RADIUS_M, analyze_inundation_impacts
from .route_planning import plan_route
from .dispatch_trial import get_longtan_dispatch_plan, simulate_longtan_dispatch


class FloodRuntimeService:
    def __init__(self, resolver: Any):
        self.resolver = resolver

    def find_nearby_objects(self, reference_object_type: str, reference_object_id: str,
                            target_type: str = "EvacuationSite", radius_m: float = 3000,
                            limit: int = 10, filters: dict | None = None,
                            min_distance_m: float = 0, offset: int = 0,
                            exclude_object_ids: list[str] | None = None) -> dict[str, Any]:
        result = find_nearby_objects(self.resolver, reference_object_type, reference_object_id,
                                     target_type, radius_m, limit, filters, min_distance_m, offset, exclude_object_ids)
        if "error" not in result:
            matching_ids = result.pop("_matched_object_ids")
            basis = {key: result[key] for key in ("reference", "radius_m", "min_distance_m", "filters", "excluded_object_ids")}
            try:
                matching = save_object_set(target_type, matching_ids, basis=basis)
                page = save_object_set(target_type, result["object_ids"], basis={**basis, "offset": offset, "limit": limit}, parent_set_id=matching["object_set_id"])
                result["matching_set"] = set_summary(matching)
                result["page_set"] = set_summary(page)
            except (OSError, ValueError):
                result.update(matching_set=None, page_set=None, matching_object_ids=matching_ids,
                              set_storage_warning="对象集合未能保存；本页使用 object_ids，全范围使用 matching_object_ids，查询数量与距离仍有效。")
        return result

    def refine_object_set(self, object_set_id: str, filters: dict | None = None,
                          exclude_object_ids: list[str] | None = None) -> dict:
        return refine_object_set(self.resolver, object_set_id, filters, exclude_object_ids)

    def compare_evacuation_sites(self, evacuation_unit_id: str, object_set_id: str = "",
                                 required_capacity: int | None = None, view: str = "current",
                                 time_h: float | None = None, forecast_id: str = "latest",
                                 object_ids: list[str] | None = None) -> dict:
        return compare_evacuation_sites(self.resolver, evacuation_unit_id, object_set_id, required_capacity, view, time_h, forecast_id, object_ids)

    def review_route(self, evacuation_route_id: str, view: str = "current", time_h: float | None = None, forecast_id: str = "latest") -> dict:
        return review_route(self.resolver, evacuation_route_id, view, time_h, forecast_id)

    def get_flood_status(self, view: str = "current", time_h: float | None = None, forecast_id: str = "latest") -> dict[str, Any]:
        return get_flood_status(view, time_h, forecast_id)

    def get_longtan_dispatch_plan(self, forecast_id: str = "latest") -> dict[str, Any]:
        context = resolve_forecast_context(forecast_id, view="envelope")
        if not context["available"]:
            return unavailable_forecast(context)
        try:
            return get_longtan_dispatch_plan(context["forecast_version"])
        except (OSError, ValueError, KeyError) as error:
            return {"status": "dispatch_unavailable", "error": str(error), "applied": False}

    def simulate_longtan_dispatch(
        self, settings: dict, forecast_id: str = "latest", time_h: float | None = None,
        target_type: str = "Road", object_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        context = resolve_forecast_context(
            forecast_id, time_h, "time_slice" if time_h not in (None, "") else "current",
        )
        if not context["available"]:
            return unavailable_forecast(context)
        try:
            return simulate_longtan_dispatch(
                self.resolver, settings, context["forecast_version"], context["time_h"],
                target_type, object_ids,
            )
        except (OSError, ValueError, KeyError) as error:
            return {"status": "dispatch_trial_unavailable", "error": str(error), "applied": False}

    def run_flood_forecast(self, forecast_id: str = "latest",
                           force: bool = False) -> dict[str, Any]:
        context = forecast_input_context()
        if not context["available"]:
            return unavailable_forecast(context)
        if forecast_id not in {"latest", "forecast_latest"}:
            return {"error": "运行预测只接受当前输入；历史预测不能作为当前运行结果。"}
        return run_flood_forecast(self.resolver, forecast_id, force)

    def assess_flood_emergency(self, refresh: bool = False, forecast_id: str = "latest") -> dict[str, Any]:
        context = resolve_forecast_context(forecast_id, view="envelope")
        if not context["available"]:
            return unavailable_forecast(context)
        return assess_flood_emergency(self.resolver, refresh, context["forecast_version"])

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
        object_set_id: str = "",
    ) -> dict[str, Any]:
        if object_set_id:
            if object_ids is not None:
                return {"error": "object_set_id 与 object_ids 不能同时提供"}
            try:
                selected = read_object_set(object_set_id, target_type if target_type != "all" else None)
                target_type, object_ids = selected["object_type"], selected["object_ids"]
            except ValueError as exc:
                return {"error": str(exc)}
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
        if object_set_id and "analysis_scope" in result:
            result["analysis_scope"]["object_set_id"] = object_set_id
        result["forecast_context"] = context
        return result


    def analyze_latest_evacuation_time(
        self,
        evacuation_unit_id: str = "",
        evacuation_unit_name: str = "",
        evacuation_route_id: str = "",
        forecast_id: str = "latest",
        blocked_depth_m: float | None = None,
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
            context["forecast_version"],
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
