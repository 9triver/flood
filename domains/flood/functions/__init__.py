from __future__ import annotations

from oag.ontology.registry import FunctionRegistry
from oag.ontology.repository import ObjectRepository
from oag.ontology.schema import Ontology

from domains.flood.runtime.evacuation_timing import analyze_latest_evacuation_time
from domains.flood.runtime.forecast import run_emergency_cycle, run_flood_forecast
from domains.flood.runtime.impact_analysis import (
    BRIDGE_INFLUENCE_RADIUS_M,
    analyze_inundation_impacts,
)
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.route_planning import plan_evacuation_route


def register(registry: FunctionRegistry, repository: ObjectRepository,
             ontology: Ontology):
    resolver = FloodRepository()
    registry.register_resolver("flood_repository", resolver)

    registry.register("run_flood_forecast", lambda forecast_id="latest", force=False: run_flood_forecast(
        resolver, forecast_id, force,
    ), ontology.functions["run_flood_forecast"])
    registry.register("run_emergency_cycle", lambda force_forecast=False: run_emergency_cycle(
        resolver, force_forecast,
    ), ontology.functions["run_emergency_cycle"])
    registry.register("analyze_inundation_impacts", lambda forecast_id="latest", target_type="all", min_depth_m=0.15, max_distance_m=10, time_h=None, bridge_influence_radius_m=BRIDGE_INFLUENCE_RADIUS_M: analyze_inundation_impacts(
        resolver, forecast_id, target_type, min_depth_m, max_distance_m, time_h,
        bridge_influence_radius_m,
    ), ontology.functions["analyze_inundation_impacts"])
    registry.register("analyze_latest_evacuation_time", lambda evacuation_unit_id="", evacuation_unit_name="", evacuation_route_id="", forecast_id="latest", blocked_depth_m=0.3, clearance_duration_min=None, safety_buffer_min=0: analyze_latest_evacuation_time(
        resolver,
        evacuation_unit_id,
        evacuation_unit_name,
        evacuation_route_id,
        forecast_id,
        blocked_depth_m,
        clearance_duration_min,
        safety_buffer_min,
    ), ontology.functions["analyze_latest_evacuation_time"])
    registry.register("plan_evacuation_route", lambda start_object_type="EvacuationUnit", start_object_id="", destination_site_id="", start_lon=None, start_lat=None, destination_lon=None, destination_lat=None, forecast_id="latest", time_h=None, blocked_depth_m=None, profile="car", avoid_flood=True, max_endpoint_distance_m=800, max_detour_ratio=10: plan_evacuation_route(
        resolver,
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
    ), ontology.functions["plan_evacuation_route"])
