from __future__ import annotations

import time

from domain_os_mvp.domains.flood.paths import (
    ASSETS_BASE,
    MODEL_PATH,
    ROUTING_PATH,
    SCENARIO_PATH,
    station_metric_path,
)
from domain_os_mvp.domains.flood.telemetry import mqtt_payload
from domain_os_mvp.domains.flood.world import build_flood_world


class ManualClock:
    def __init__(self, value: float = 1_800_000_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 1.0) -> float:
        self.value += seconds
        return self.value


def pump_until(world, predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        world.kernel.pump()
        result = predicate()
        if result is not None:
            return result
        time.sleep(0.005)
    raise TimeoutError("domain product was not published")


def test_world_mounts_real_gis_assets_and_capability_resources(tmp_path):
    database = tmp_path / "world.sqlite"
    world = build_flood_world(database)
    agent = world.agent_client()

    catalog = agent.read(ASSETS_BASE).value
    assert catalog["object_count"] == 1666
    assert catalog["counts"]["EvacuationSite"] == 719
    assert len(agent.list_asset_refs("Road")) == 423
    watershed = agent.read(agent.list_asset_refs("Watershed")[0]).value
    assert watershed["attributes"]["name"] == "珊瑚河流域"
    assert watershed["geometry"]["type"] in {"Polygon", "MultiPolygon"}

    assert agent.read(MODEL_PATH).value["kind"] == "model"
    assert agent.read(ROUTING_PATH).value["provider"] == "amap"
    assert agent.read(SCENARIO_PATH).value["kind"] == "scenario"
    journal_count = world.kernel.store.journal_count
    world.close()

    recovered = build_flood_world(database)
    assert recovered.kernel.read(ASSETS_BASE).value["object_count"] == 1666
    # Session capabilities are new, but all domain resources are recovered
    # instead of being ingested as duplicate observations.
    assert recovered.kernel.store.journal_count == journal_count + 4
    recovered.close()


def test_mqtt_scenario_model_route_and_agent_assessment_share_one_world(tmp_path):
    clock = ManualClock()
    world = build_flood_world(tmp_path / "world.sqlite", clock=clock)
    agent = world.agent_client()

    for station_id in ("upstream", "interval1", "interval2", "tonggu"):
        world.telemetry.ingest(
            f"water/stations/{station_id}/telemetry",
            mqtt_payload(
                station_id,
                observed_at=clock.advance(),
                flow_m3s=60.0,
                message_id=f"peak-{station_id}",
            ),
        )
    world.kernel.pump()
    assert len(world.scenario_events) == 1
    assert agent.read(station_metric_path("upstream", "flow_m3s")).value["value"] == 60

    forecast = pump_until(world, lambda: agent.latest_product("forecast"))
    forecast_operation = agent.operation(world.scenario_events[0].operation_id)
    assert forecast_operation.state == "committed"
    assert forecast.value["data"]["result"]["stats"]["inundation_expected"] is True
    assert forecast.value["data"]["result"]["is_surrogate"] is True

    start_ref = agent.list_asset_refs("EvacuationUnit")[0]
    destination_ref = agent.list_asset_refs("EvacuationSite")[0]
    route_result = agent.plan_route(
        start_ref,
        destination_ref,
        profile="foot",
        forecast_ref=forecast.path,
    )
    route = pump_until(
        world,
        lambda: (
            agent.latest_product("route")
            if agent.operation(route_result.operation_id).terminal
            else None
        ),
    )
    assert agent.operation(route_result.operation_id).state == "committed"
    assert route.value["data"]["route"]["provider"] == "offline-direct-surrogate"
    assert route.value["data"]["forecast_ref"] == forecast.path

    assessment_result = agent.publish_assessment(
        basis_refs=[forecast.path, route.path],
        conclusions=[{"subject_ref": start_ref, "risk": "high"}],
        recommendations=[{"action": "prepare_evacuation"}],
        agent={"runtime": "test-agent", "model": "fake"},
    )
    assessment = pump_until(
        world,
        lambda: (
            agent.latest_product("assessment")
            if agent.operation(assessment_result.operation_id).terminal
            else None
        ),
    )
    assert agent.operation(assessment_result.operation_id).state == "committed"
    assert assessment.value["data"]["basis_refs"] == [forecast.path, route.path]
    assert assessment.value["data"]["source"] == "agent"
    world.close()
