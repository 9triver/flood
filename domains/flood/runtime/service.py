"""Application-facing facade for deterministic flood-domain functions.

The individual runtime modules remain focused on their calculations.  This
facade is the composition boundary used by OAG registration, so function
wiring does not need to know the implementation module of every operation.
"""

from __future__ import annotations

from typing import Any

from .evacuation_timing import analyze_latest_evacuation_time
from .forecast import run_emergency_cycle, run_flood_forecast
from .impact_analysis import BRIDGE_INFLUENCE_RADIUS_M, analyze_inundation_impacts
from .route_planning import plan_evacuation_route


class FloodRuntimeService:
    def __init__(self, resolver: Any):
        self.resolver = resolver

    def run_flood_forecast(self, forecast_id: str = "latest",
                           force: bool = False) -> dict[str, Any]:
        return run_flood_forecast(self.resolver, forecast_id, force)

    def run_emergency_cycle(self, force_forecast: bool = False) -> dict[str, Any]:
        return run_emergency_cycle(self.resolver, force_forecast)

    def analyze_inundation_impacts(
        self,
        forecast_id: str = "latest",
        target_type: str = "all",
        min_depth_m: float = 0.15,
        max_distance_m: float = 10,
        time_h: float | None = None,
        bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
    ) -> dict[str, Any]:
        return analyze_inundation_impacts(
            self.resolver,
            forecast_id,
            target_type,
            min_depth_m,
            max_distance_m,
            time_h,
            bridge_influence_radius_m,
        )

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

    def plan_evacuation_route(
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
        avoid_flood: bool = True,
        max_endpoint_distance_m: float = 800,
        max_detour_ratio: float = 10,
    ) -> dict[str, Any]:
        return plan_evacuation_route(
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
            avoid_flood,
            max_endpoint_distance_m,
            max_detour_ratio,
        )


__all__ = ["FloodRuntimeService"]
