from __future__ import annotations

from oag.ontology.registry import FunctionRegistry
from oag.ontology.repository import ObjectRepository
from oag.ontology.schema import Ontology

from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.service import FloodRuntimeService


def register(registry: FunctionRegistry, repository: ObjectRepository,
             ontology: Ontology):
    resolver = FloodRepository()
    runtime = FloodRuntimeService(resolver)
    registry.register_resolver("flood_repository", resolver)

    registry.register("get_flood_status", runtime.get_flood_status, ontology.functions["get_flood_status"])
    registry.register("find_nearby_objects", runtime.find_nearby_objects, ontology.functions["find_nearby_objects"])

    registry.register(
        "run_flood_forecast",
        runtime.run_flood_forecast,
        ontology.functions["run_flood_forecast"],
    )
    registry.register(
        "assess_flood_emergency",
        runtime.assess_flood_emergency,
        ontology.functions["assess_flood_emergency"],
    )
    registry.register(
        "analyze_inundation_impacts",
        runtime.analyze_inundation_impacts,
        ontology.functions["analyze_inundation_impacts"],
    )
    registry.register(
        "analyze_latest_evacuation_time",
        runtime.analyze_latest_evacuation_time,
        ontology.functions["analyze_latest_evacuation_time"],
    )
    registry.register(
        "plan_route",
        runtime.plan_route,
        ontology.functions["plan_route"],
    )
