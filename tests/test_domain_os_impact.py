from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from domain_os import (
    CommandState,
    DerivedProduct,
    Intent,
    SqliteDomainStore,
    new_id,
    utc_now,
)
from domains.flood.forecast_domain import (
    FORECAST_PRODUCT,
    WATERSHED_RESOURCE_ID,
)
from domains.flood.impact_domain import (
    IMPACT_FAILED_EVENT,
    IMPACT_GENERATED_EVENT,
    IMPACT_MODEL_RESOURCE_ID,
    IMPACT_PRODUCT,
    IMPACT_REQUIRED_EVENT,
    RUN_IMPACT_ANALYSIS,
    _product_depths,
    create_flood_impact_domain_system,
)
from domains.flood.runtime.boundary_flow import BOUNDARIES
from domains.flood.runtime.impact_analysis import BRIDGE_INFLUENCE_RADIUS_M


class FakeForecastRunner:
    def __init__(self, *, flooded_count: int = 2) -> None:
        self.flooded_count = flooded_count
        self.calls: list[dict] = []

    def __call__(self, forecast_input: dict, target: Path) -> dict:
        self.calls.append(forecast_input)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "cell_id,max_depth\n1,0.8\n2,0.4\n",
            encoding="utf-8",
        )
        target.with_name("depth_series.npy").write_bytes(b"fake-series")
        target.with_name("time_steps.json").write_text(
            json.dumps({"time_steps_h": [0.5, 1.0]}),
            encoding="utf-8",
        )
        return {
            "status": "completed",
            "model_name": "FAKE_FLOOD_MODEL",
            "flooded_count": self.flooded_count,
            "max_depth_m": 0.8 if self.flooded_count else 0,
            "mean_depth_m": 0.6 if self.flooded_count else 0,
            "time_step_count": 2,
            "time_steps_h": [0.5, 1.0],
            "device": "test",
            "_positive_depths": ({1: 0.8, 2: 0.4} if self.flooded_count else {}),
        }


class FakeImpactRunner:
    def __init__(
        self,
        *,
        error: str | None = None,
        status: str = "completed",
        impacts: list[dict] | None = None,
    ) -> None:
        self.error = error
        self.status = status
        self.impacts = impacts if impacts is not None else [{
            "object_type": "Facility",
            "object_id": "facility-1",
            "name": "Test school",
            "risk_level": "high",
            "depth_m": 0.8,
        }]
        self.calls: list[tuple[str, dict]] = []

    def __call__(
        self,
        forecast_product,
        parameters: dict,
    ) -> dict:
        self.calls.append((forecast_product.product_id, dict(parameters)))
        if self.error:
            return {"error": self.error}
        summary = {
            "Facility": {
                "count": len(self.impacts),
                "critical": 0,
                "high": len(self.impacts),
                "medium": 0,
                "low": 0,
                "max_depth_m": 0.8 if self.impacts else 0,
            },
        }
        return {
            "status": self.status,
            "forecast_id": forecast_product.product_id,
            "time_h": parameters["time_h"],
            "target_type": parameters["target_type"],
            "summary": summary,
            "affected_object_ids": {
                "Facility": [item["object_id"] for item in self.impacts],
            },
            "total_impacts": len(self.impacts),
            "basis": "deterministic test impact analysis",
            "impacts": list(self.impacts),
            "object_library_version": "test-library-v1",
            "forecast_cell_count_analyzed": 2,
        }


class DomainOSFloodImpactTest(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_product_triggers_versioned_impact_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forecast_runner = FakeForecastRunner()
            impact_runner = FakeImpactRunner()
            system = create_flood_impact_domain_system(
                rows=evolution_rows(peak_by_sequence={24: 240.0}),
                forecast_runner=forecast_runner,
                impact_runner=impact_runner,
                artifact_root=root / "products",
                evolution_run_id="impact-run",
            )
            await system.start()
            try:
                await system.advance()

                forecasts = system.runtime.products(product_type=FORECAST_PRODUCT)
                impacts = system.runtime.products(product_type=IMPACT_PRODUCT)
                self.assertEqual(1, len(forecasts))
                self.assertEqual(1, len(impacts))
                impact = impacts[0]
                self.assertEqual((forecasts[0].product_id,), impact.input_refs)
                self.assertEqual(1, impact.data["total_impacts"])
                self.assertEqual(
                    ["facility-1"],
                    impact.data["affected_object_ids"]["Facility"],
                )
                self.assertEqual("test-library-v1", impact.data["object_library_version"])
                self.assertEqual(
                    IMPACT_MODEL_RESOURCE_ID,
                    impact.producer_id,
                )
                report_path = Path(impact.artifacts["impact_report"])
                self.assertTrue(report_path.exists())
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(1, report["total_impacts"])
                self.assertEqual(
                    forecasts[0].product_id,
                    report["forecast_product_id"],
                )
                self.assertEqual(
                    impact.data["analysis_signature"],
                    report["analysis_signature"],
                )

                commands = system.runtime.commands()
                self.assertEqual(2, len(commands))
                self.assertTrue(all(
                    command.state is CommandState.CONFIRMED
                    for command in commands
                ))
                self.assertEqual(
                    "water.rule.forecast-impact-analysis",
                    commands[-1].intent.actor_id,
                )
                self.assertEqual(
                    1,
                    len(system.runtime.events(event_type=IMPACT_REQUIRED_EVENT)),
                )
                self.assertEqual(
                    1,
                    len(system.runtime.events(event_type=IMPACT_GENERATED_EVENT)),
                )
                self.assertNotIn(
                    "affected_object_ids",
                    system.runtime.projection(WATERSHED_RESOURCE_ID),
                )

                repeated = await system.runtime.submit_intent(Intent(
                    intent_id=new_id("intent"),
                    actor_id="test.replay",
                    resource_id=IMPACT_MODEL_RESOURCE_ID,
                    capability_id=RUN_IMPACT_ANALYSIS,
                    arguments={
                        "forecast_product_id": forecasts[0].product_id,
                        "target_type": "all",
                        "min_depth_m": 0.15,
                        "max_distance_m": 10.0,
                        "bridge_influence_radius_m": BRIDGE_INFLUENCE_RADIUS_M,
                    },
                    requested_at=utc_now(),
                ))
                self.assertEqual(CommandState.CONFIRMED, repeated.state)
                self.assertTrue(repeated.output["reused"])
                self.assertEqual(1, len(impact_runner.calls))
                self.assertEqual(3, len(system.runtime.products()))

                changed_parameters = await system.runtime.submit_intent(Intent(
                    intent_id=new_id("intent"),
                    actor_id="test.parameter-change",
                    resource_id=IMPACT_MODEL_RESOURCE_ID,
                    capability_id=RUN_IMPACT_ANALYSIS,
                    arguments={
                        "forecast_product_id": forecasts[0].product_id,
                        "target_type": "Facility",
                        "min_depth_m": 0.2,
                        "max_distance_m": 10.0,
                    },
                    requested_at=utc_now(),
                ))
                self.assertEqual(CommandState.CONFIRMED, changed_parameters.state)
                self.assertEqual(2, len(impact_runner.calls))
                self.assertEqual(4, len(system.runtime.products()))
                self.assertNotEqual(
                    impact.product_id,
                    changed_parameters.output["product_id"],
                )

                time_slice = await system.runtime.submit_intent(Intent(
                    intent_id=new_id("intent"),
                    actor_id="test.time-slice",
                    resource_id=IMPACT_MODEL_RESOURCE_ID,
                    capability_id=RUN_IMPACT_ANALYSIS,
                    arguments={
                        "forecast_product_id": forecasts[0].product_id,
                        "time_h": 1.0,
                    },
                    requested_at=utc_now(),
                ))
                slice_product = system.runtime.product(
                    str(time_slice.output["product_id"]),
                )
                expected_valid_at = forecasts[0].valid_from + timedelta(hours=1)
                self.assertEqual(expected_valid_at, slice_product.valid_from)
                self.assertEqual(expected_valid_at, slice_product.valid_to)
                self.assertEqual(3, len(impact_runner.calls))
                self.assertEqual(5, len(system.runtime.products()))
            finally:
                await system.stop()

    async def test_time_slice_reads_nearest_explicit_product_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            max_depth = root / "max_depth.csv"
            series = root / "depth_series.npy"
            steps = root / "time_steps.json"
            max_depth.write_text(
                "cell_id,max_depth\n1,0.9\n2,0.0\n3,0.2\n",
                encoding="utf-8",
            )
            np.save(series, np.asarray([
                [0.0, 0.3, 0.0],
                [0.1, 0.0, 0.7],
            ], dtype=np.float32))
            steps.write_text(
                json.dumps({"time_steps_h": [0.5, 1.0]}),
                encoding="utf-8",
            )
            now = utc_now()
            product = DerivedProduct(
                product_id="water.flood.forecast/test/000001",
                product_type=FORECAST_PRODUCT,
                subject_id=WATERSHED_RESOURCE_ID,
                producer_id="water.model/test",
                generated_at=now,
                valid_from=now,
                valid_to=now + timedelta(hours=24),
                artifacts={
                    "max_depth": str(max_depth),
                    "depth_series": str(series),
                    "time_steps": str(steps),
                },
            )

            envelope, envelope_time = _product_depths(product, None)
            sliced, actual_time = _product_depths(product, 0.8)

            self.assertEqual({1: 0.9, 3: 0.2}, envelope)
            self.assertIsNone(envelope_time)
            self.assertEqual(1.0, actual_time)
            self.assertAlmostEqual(0.1, sliced[1], places=6)
            self.assertAlmostEqual(0.7, sliced[3], places=6)

    async def test_empty_analysis_is_still_a_traceable_product(self):
        with tempfile.TemporaryDirectory() as directory:
            impact_runner = FakeImpactRunner(
                status="no_forecast_cells",
                impacts=[],
            )
            system = create_flood_impact_domain_system(
                rows=evolution_rows(peak_by_sequence={24: 240.0}),
                forecast_runner=FakeForecastRunner(flooded_count=0),
                impact_runner=impact_runner,
                artifact_root=Path(directory) / "products",
                evolution_run_id="dry-impact-run",
            )
            await system.start()
            try:
                await system.advance()

                impact = system.runtime.products(product_type=IMPACT_PRODUCT)[0]
                self.assertEqual("no_forecast_cells", impact.data["status"])
                self.assertEqual(0, impact.data["total_impacts"])
                self.assertEqual(CommandState.CONFIRMED, system.runtime.commands()[-1].state)
            finally:
                await system.stop()

    async def test_impact_failure_does_not_create_assessment_product(self):
        with tempfile.TemporaryDirectory() as directory:
            system = create_flood_impact_domain_system(
                rows=evolution_rows(peak_by_sequence={24: 240.0}),
                forecast_runner=FakeForecastRunner(),
                impact_runner=FakeImpactRunner(error="object library unavailable"),
                artifact_root=Path(directory) / "products",
                evolution_run_id="failed-impact-run",
            )
            await system.start()
            try:
                await system.advance()

                self.assertEqual((), system.runtime.products(product_type=IMPACT_PRODUCT))
                command = system.runtime.commands()[-1]
                self.assertEqual(CommandState.FAILED, command.state)
                self.assertEqual("object library unavailable", command.error)
                failed = system.runtime.events(event_type=IMPACT_FAILED_EVENT)
                self.assertEqual(1, len(failed))
                self.assertEqual(command.command_id, failed[0].data["command_id"])
            finally:
                await system.stop()

    async def test_impact_product_and_events_restore_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "domain.sqlite"
            rows = evolution_rows(peak_by_sequence={24: 240.0})
            first_store = SqliteDomainStore(database)
            first = create_flood_impact_domain_system(
                rows=rows,
                store=first_store,
                forecast_runner=FakeForecastRunner(),
                impact_runner=FakeImpactRunner(),
                artifact_root=root / "products",
                evolution_run_id="persistent-impact-run",
            )
            await first.start()
            await first.advance()
            product_ids = [item.product_id for item in first.runtime.products()]
            await first.stop()
            first_store.close()

            second_store = SqliteDomainStore(database)
            second = create_flood_impact_domain_system(
                rows=rows,
                store=second_store,
                forecast_runner=FakeForecastRunner(),
                impact_runner=FakeImpactRunner(),
                artifact_root=root / "products",
                evolution_run_id="persistent-impact-run",
            )
            await second.start()
            try:
                self.assertEqual(
                    product_ids,
                    [item.product_id for item in second.runtime.products()],
                )
                self.assertEqual(
                    1,
                    len(second.runtime.events(event_type=IMPACT_GENERATED_EVENT)),
                )
                self.assertEqual(2, len(second.runtime.commands()))
                self.assertTrue(all(
                    command.state is CommandState.CONFIRMED
                    for command in second.runtime.commands()
                ))
            finally:
                await second.stop()
                second_store.close()


def evolution_rows(
    *,
    count: int = 25,
    peak_by_sequence: dict[int, float] | None = None,
) -> list[dict]:
    start = datetime(2026, 8, 26, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    peaks = peak_by_sequence or {}
    rows = []
    for sequence in range(count):
        total_flow = float(peaks.get(sequence, 40.0))
        boundary_flow = total_flow / len(BOUNDARIES)
        observed_at = (start + timedelta(hours=sequence)).isoformat()
        rows.append({
            "sequence": sequence,
            "observed_at": observed_at,
            "simulation_time": observed_at,
            "rainfall_mm": float(sequence % 4),
            "reservoir_inflow_m3s": 30.0 + sequence,
            "reservoir_release_m3s": 20.0 + sequence,
            "reservoir_level_m": 245.1 + sequence * 0.01,
            "boundaries": {
                key: {"label": label, "flow_m3s": boundary_flow}
                for key, label in BOUNDARIES.items()
            },
            "total_flow_m3s": total_flow,
        })
    return rows


if __name__ == "__main__":
    unittest.main()
