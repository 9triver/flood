from __future__ import annotations

from oag.ontology.registry import FunctionRegistry
from oag.ontology.repository import ObjectRepository
from oag.ontology.schema import Ontology

from domains.flood.runtime.geojson import export_objects_geojson
from domains.flood.runtime.forecast import run_emergency_cycle, run_flood_forecast
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.tools import (
    list_mappable_objects,
    not_wired,
    scenario_summary,
)


def register(registry: FunctionRegistry, repository: ObjectRepository,
             ontology: Ontology):
    resolver = FloodRepository()
    registry.register_resolver("flood_repository", resolver)

    registry.register("list_scenarios", lambda: resolver.scenarios,
                      ontology.functions["list_scenarios"])
    registry.register("get_scenario_summary", lambda scenario_id="", return_period_year=0: scenario_summary(
        resolver, scenario_id, return_period_year,
    ), ontology.functions["get_scenario_summary"])
    registry.register("run_flood_forecast", lambda forecast_id="latest", force=False: run_flood_forecast(
        resolver, forecast_id, force,
    ), ontology.functions["run_flood_forecast"])
    registry.register("run_emergency_cycle", lambda force_forecast=False: run_emergency_cycle(
        resolver, force_forecast,
    ), ontology.functions["run_emergency_cycle"])
    registry.register("analyze_inundation_impacts", lambda forecast_id="latest", target_type="all", min_depth_m=0.15, max_distance_m=120: analyze_inundation_impacts(
        resolver, forecast_id, target_type, min_depth_m, max_distance_m,
    ), ontology.functions["analyze_inundation_impacts"])
    registry.register("analyze_risks", not_wired("analyze_risks"),
                      ontology.functions["analyze_risks"])
    registry.register("list_mappable_objects", lambda object_type="": list_mappable_objects(
        resolver, object_type,
    ), ontology.functions["list_mappable_objects"])
    registry.register("export_objects_geojson", lambda object_type, filters=None, simplify_tolerance=0, force=False: export_objects_geojson(
        resolver, object_type, filters or {}, simplify_tolerance, force,
    ), ontology.functions["export_objects_geojson"])
    registry.register("plan_response", not_wired("plan_response"),
                      ontology.functions["plan_response"])
    registry.register("generate_brief", not_wired("generate_brief"),
                      ontology.functions["generate_brief"])
