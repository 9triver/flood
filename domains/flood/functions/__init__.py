from __future__ import annotations

from oag.ontology.registry import FunctionRegistry
from oag.ontology.repository import ObjectRepository
from oag.ontology.schema import Ontology

from domains.flood.runtime.forecast import run_emergency_cycle, run_flood_forecast
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.repository import FloodRepository


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
    registry.register("analyze_inundation_impacts", lambda forecast_id="latest", target_type="all", min_depth_m=0.15, max_distance_m=120, time_h=None: analyze_inundation_impacts(
        resolver, forecast_id, target_type, min_depth_m, max_distance_m, time_h,
    ), ontology.functions["analyze_inundation_impacts"])
