from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np

from domain_os import (
    Capability,
    Command,
    CommandResult,
    DerivedProduct,
    DomainQueryService,
    DomainRecordNotFound,
    DomainRuntime,
    DriverHealth,
    Intent,
    Observation,
    ObservationQuality,
    Resource,
    RiskBasedPolicy,
    utc_now,
)
from domains.flood.forecast_domain import (
    FORECAST_INPUT_PRODUCT,
    FORECAST_PRODUCT,
    WATERSHED_RESOURCE_ID,
)
from domains.flood.impact_domain import IMPACT_PRODUCT
from domains.flood.product_views import FloodProductViews
from server.domain_api import DomainApi


class QueryDriver:
    driver_id = "test.query-driver"

    def __init__(self) -> None:
        self.sink = None

    async def start(self, sink) -> None:
        self.sink = sink

    async def stop(self) -> None:
        self.sink = None

    async def execute(self, command: Command) -> CommandResult:
        return CommandResult(accepted=False, error="not supported")

    def health(self) -> DriverHealth:
        return DriverHealth(
            driver_id=self.driver_id,
            connected=self.sink is not None,
            checked_at=utc_now(),
        )


class FakeMesh:
    def __init__(self) -> None:
        self.meta_calls = []
        self.tile_calls = []

    def meta_from_depths(self, forecast, depths):
        self.meta_calls.append((dict(forecast), dict(depths)))
        return {"forecast": dict(forecast), "depths": dict(depths)}

    def tile_from_depths(self, z, x, y, depths, **options):
        self.tile_calls.append((z, x, y, dict(depths), dict(options)))
        return {
            "forecast_id": options["source_id"],
            "time_h": options["time_h"],
            "time_index": options["time_index"],
            "depths": dict(depths),
        }


class DomainQueryApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.driver = QueryDriver()
        self.runtime = DomainRuntime(
            domain_id="test.query",
            policy=RiskBasedPolicy(),
        )
        self.runtime.register_driver(self.driver)
        self.runtime.register_capability(Capability(
            capability_id="test.noop",
            description="No-op capability",
        ))
        self.runtime.register_resource(Resource(
            resource_id="test.resource/one",
            resource_type="test.resource",
            name="Test resource",
            driver_id=self.driver.driver_id,
            capabilities=frozenset({"test.noop"}),
        ))
        await self.runtime.start()
        self.queries = DomainQueryService(self.runtime)

    async def asyncTearDown(self) -> None:
        self.queries.close()
        await self.runtime.stop()

    async def test_queries_projection_products_and_event_timeline(self):
        now = utc_now()
        await self.runtime.ingest(Observation(
            observation_id="observation-1",
            resource_id="test.resource/one",
            metric="level",
            value=3.2,
            unit="m",
            observed_at=now,
            received_at=now,
            quality=ObservationQuality.GOOD,
        ), driver_id=self.driver.driver_id)
        product = DerivedProduct(
            product_id="test.product/one",
            product_type="test.product",
            subject_id="test.resource/one",
            producer_id="test.producer",
            generated_at=now,
            data={"answer": 42},
        )
        await self.runtime.record_product(product)
        completed_event = await self.runtime.publish_event(
            "test.completed",
            "test.resource/one",
            {"product_id": product.product_id},
        )

        projections = self.queries.projections(resource_id="test.resource/one")
        products = self.queries.products(product_type="test.product")
        events = self.queries.events(after=0, event_type="test.completed")

        self.assertEqual(3.2, projections["items"][0]["values"]["level"]["value"])
        self.assertEqual("test.product/one", products["items"][0]["product_id"])
        self.assertEqual("test.completed", events["items"][0]["event"]["event_type"])
        self.assertEqual(len(self.runtime.events()), events["next_cursor"])
        self.assertEqual(product.product_id, self.queries.product(product.product_id)["product_id"])
        self.assertEqual(
            completed_event.event_id,
            self.queries.event(completed_event.event_id)["event_id"],
        )
        with self.assertRaises(DomainRecordNotFound):
            self.queries.product("missing")
        with self.assertRaises(DomainRecordNotFound):
            self.queries.event("missing")

    async def test_queries_command_history_with_domain_filters(self):
        intent = Intent(
            intent_id="intent-query-1",
            actor_id="agent-query-test",
            resource_id="test.resource/one",
            capability_id="test.noop",
            arguments={"value": 3},
            requested_at=utc_now(),
            rationale="Verify command query",
            correlation_id="query-correlation",
        )
        command = await self.runtime.submit_intent(intent)

        result = self.queries.commands(
            state=command.state.value,
            resource_id=intent.resource_id,
            actor_id=intent.actor_id,
            capability_id=intent.capability_id,
        )
        selected = self.queries.command(command.command_id)

        self.assertEqual(1, result["total"])
        self.assertEqual(command.command_id, result["items"][0]["command_id"])
        self.assertEqual(intent.intent_id, selected["intent"]["intent_id"])
        self.assertEqual("query-correlation", selected["intent"]["correlation_id"])
        with self.assertRaises(DomainRecordNotFound):
            self.queries.command("missing")
        with self.assertRaisesRegex(ValueError, "unknown command state"):
            self.queries.commands(state="made_up")

    async def test_filtered_wait_advances_cursor_and_wakes_for_matching_event(self):
        await self.runtime.publish_event("test.ignored", "test.resource/one", {})
        initial = self.queries.events(event_type="test.match")
        self.assertEqual([], initial["items"])
        self.assertEqual(len(self.runtime.events()), initial["next_cursor"])

        wait = asyncio.create_task(asyncio.to_thread(
            self.queries.wait_for_events,
            after=initial["next_cursor"],
            event_type="test.match",
            timeout=2,
        ))
        await asyncio.sleep(0.01)
        await self.runtime.publish_event("test.match", "test.resource/one", {"ok": True})
        result = await wait

        self.assertEqual(1, result["count"])
        self.assertTrue(result["items"][0]["event"]["data"]["ok"])

    async def test_sse_uses_domain_event_cursor_as_event_id(self):
        await self.runtime.publish_event("test.ready", "test.resource/one", {})
        api = DomainApi(self.queries)

        chunk = next(api.stream_events(after=0, heartbeat_seconds=0.01))

        self.assertIn(b"id: 1\n", chunk)
        self.assertIn(b"event: domain_event\n", chunk)
        self.assertIn(b'"event_type": "test.ready"', chunk)


class FloodProductViewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_and_impact_views_use_explicit_products(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            max_depth = root / "max_depth.csv"
            depth_series = root / "depth_series.npy"
            time_steps = root / "time_steps.json"
            max_depth.write_text(
                "cell_id,max_depth\n1,0.9\n2,0.0\n3,0.2\n",
                encoding="utf-8",
            )
            np.save(depth_series, np.asarray([
                [0.0, 0.3, 0.0],
                [0.1, 0.0, 0.7],
            ], dtype=np.float32))
            time_steps.write_text(
                json.dumps({"time_steps_h": [0.5, 1.0]}),
                encoding="utf-8",
            )
            driver = QueryDriver()
            runtime = DomainRuntime(
                domain_id="water.flood",
                policy=RiskBasedPolicy(),
            )
            runtime.register_driver(driver)
            runtime.register_resource(Resource(
                resource_id=WATERSHED_RESOURCE_ID,
                resource_type="water.watershed",
                name="Shanhu watershed",
                driver_id=driver.driver_id,
            ))
            await runtime.start()
            queries = DomainQueryService(runtime)
            try:
                now = utc_now()
                input_product = DerivedProduct(
                    product_id="water.flood.forecast-input/test/000001",
                    product_type=FORECAST_INPUT_PRODUCT,
                    subject_id=WATERSHED_RESOURCE_ID,
                    producer_id="water.evolution/test",
                    generated_at=now,
                    valid_from=now,
                    valid_to=now + timedelta(hours=24),
                    data={
                        "summary": {
                            "rainfall_series": [{
                                "time_h": 0,
                                "valid_time": now.isoformat(),
                                "rainfall_mm": 2.5,
                            }],
                        },
                    },
                )
                forecast = DerivedProduct(
                    product_id="water.flood.forecast/test/000001",
                    product_type=FORECAST_PRODUCT,
                    subject_id=WATERSHED_RESOURCE_ID,
                    producer_id="water.model/test",
                    generated_at=now,
                    valid_from=now,
                    valid_to=now + timedelta(hours=24),
                    input_refs=(input_product.product_id,),
                    data={
                        "status": "completed",
                        "time_steps_h": [0.5, 1.0],
                    },
                    artifacts={
                        "max_depth": str(max_depth),
                        "depth_series": str(depth_series),
                        "time_steps": str(time_steps),
                    },
                )
                impact = DerivedProduct(
                    product_id="water.flood.impact-assessment/test/default",
                    product_type=IMPACT_PRODUCT,
                    subject_id=WATERSHED_RESOURCE_ID,
                    producer_id="water.model/impact-test",
                    generated_at=now,
                    valid_from=now,
                    valid_to=now + timedelta(hours=24),
                    input_refs=(forecast.product_id,),
                    data={
                        "status": "completed",
                        "forecast_id": forecast.product_id,
                        "total_impacts": 1,
                        "impacts": [{"object_id": "bridge-1"}],
                        "parameters": {
                            "target_type": "all",
                            "min_depth_m": 0.15,
                            "max_distance_m": 10.0,
                            "bridge_influence_radius_m": 80.0,
                            "time_h": None,
                        },
                    },
                )
                for product in (input_product, forecast, impact):
                    await runtime.record_product(product)

                mesh = FakeMesh()
                views = FloodProductViews(queries, mesh=mesh)
                meta = views.forecast_grid_meta(forecast.product_id)
                tile = views.forecast_grid_tile(
                    13,
                    6789,
                    3456,
                    forecast.product_id,
                    wet_only=True,
                    time_h=0.8,
                )
                assessment = views.impact_for_forecast(forecast.product_id)

                self.assertEqual({1: 0.9, 3: 0.2}, meta["depths"])
                self.assertEqual(2.5, meta["forecast"]["rainfall_series"][0]["rainfall_mm"])
                self.assertEqual(forecast.product_id, meta["forecast"]["forecast_id"])
                self.assertEqual(1.0, tile["time_h"])
                self.assertEqual(1, tile["time_index"])
                self.assertAlmostEqual(0.1, tile["depths"][1], places=6)
                self.assertAlmostEqual(0.7, tile["depths"][3], places=6)
                self.assertEqual(impact.product_id, assessment["assessment_product_id"])
                self.assertEqual(forecast.product_id, assessment["forecast_product_id"])
            finally:
                queries.close()
                await runtime.stop()


if __name__ == "__main__":
    unittest.main()
