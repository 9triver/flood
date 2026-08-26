from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from domain_os import (
    CommandState,
    Intent,
    Observation,
    ObservationQuality,
    SqliteDomainStore,
    new_id,
    utc_now,
)
from domains.flood.forecast_domain import (
    BOUNDARY_RESOURCE_IDS,
    EVOLUTION_DRIVER_ID,
    EVOLUTION_RESOURCE_ID,
    FORECAST_FAILED_EVENT,
    FORECAST_GENERATED_EVENT,
    FORECAST_INPUT_PRODUCT,
    FORECAST_PRODUCT,
    FORECAST_REQUIRED_EVENT,
    FORECAST_MODEL_RESOURCE_ID,
    RESERVOIR_RESOURCE_ID,
    RUN_FLOOD_FORECAST,
    WATERSHED_RESOURCE_ID,
    create_flood_forecast_domain_system,
)
from domains.flood.runtime.boundary_flow import BOUNDARIES


class FakeForecastRunner:
    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, forecast_input: dict, target: Path) -> dict:
        self.calls.append(forecast_input)
        if self.error:
            return {"error": self.error, "detail": "fake model failure"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "cell_id,max_depth\n1,0.8\n2,0.0\n",
            encoding="utf-8",
        )
        target.with_name("depth_series.npy").write_bytes(b"fake-depth-series")
        target.with_name("time_steps.json").write_text(
            json.dumps({"time_steps_h": [0.5, 1.0]}),
            encoding="utf-8",
        )
        return {
            "status": "completed",
            "model_name": "FAKE_FLOOD_MODEL",
            "model_description": "Deterministic test forecast",
            "flooded_count": 1,
            "max_depth_m": 0.8,
            "mean_depth_m": 0.8,
            "time_step_count": 2,
            "time_steps_h": [0.5, 1.0],
            "device": "test",
            "timings_ms": {"total": 1.0},
            "_positive_depths": {1: 0.8},
        }


class DomainOSFloodForecastTest(unittest.IsolatedAsyncioTestCase):
    async def test_current_facts_trigger_governed_forecast_product(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeForecastRunner()
            rows = evolution_rows(peak_by_sequence={24: 240.0})
            system = create_flood_forecast_domain_system(
                rows=rows,
                runner=runner,
                artifact_root=Path(directory) / "products",
                evolution_run_id="test-run",
            )
            await system.start()
            try:
                await system.advance()

                observations = system.runtime.observations()
                self.assertEqual(10, len(observations))
                self.assertEqual(
                    {rows[0]["observed_at"]},
                    {item.observed_at.isoformat() for item in observations},
                )
                self.assertEqual(
                    rows[0]["boundaries"]["interval1"]["flow_m3s"],
                    system.runtime.projection(
                        BOUNDARY_RESOURCE_IDS["interval1"]
                    )["flow_m3s"].value,
                )
                self.assertEqual(
                    rows[0]["reservoir_level_m"],
                    system.runtime.projection(RESERVOIR_RESOURCE_ID)[
                        "water_level_m"
                    ].value,
                )

                inputs = system.runtime.products(product_type=FORECAST_INPUT_PRODUCT)
                forecasts = system.runtime.products(product_type=FORECAST_PRODUCT)
                self.assertEqual(1, len(inputs))
                self.assertEqual(1, len(forecasts))
                input_product = inputs[0]
                forecast = forecasts[0]
                self.assertEqual(25, input_product.data["summary"]["forecast_point_count"])
                self.assertEqual(
                    timedelta(hours=24),
                    input_product.valid_to - input_product.valid_from,
                )
                self.assertEqual((input_product.product_id,), forecast.input_refs)
                self.assertEqual(1, forecast.data["forecast_cell_count"])
                self.assertEqual(0.8, forecast.data["max_depth_m"])
                self.assertEqual(
                    {"max_depth", "depth_series", "time_steps"},
                    set(forecast.artifacts),
                )

                self.assertEqual(1, len(runner.calls))
                self.assertEqual(
                    input_product.product_id,
                    runner.calls[0]["summary"]["boundary_flow_id"],
                )
                command = system.runtime.commands()[0]
                self.assertEqual(CommandState.CONFIRMED, command.state)
                self.assertEqual("water.rule.forecast-trigger", command.intent.actor_id)
                self.assertEqual(forecast.product_id, command.output["product_id"])
                self.assertEqual(
                    1,
                    len(system.runtime.events(event_type=FORECAST_REQUIRED_EVENT)),
                )
                self.assertEqual(
                    1,
                    len(system.runtime.events(event_type=FORECAST_GENERATED_EVENT)),
                )
                self.assertNotIn(
                    "max_depth_m",
                    system.runtime.projection(WATERSHED_RESOURCE_ID),
                )

                repeated = await system.runtime.submit_intent(Intent(
                    intent_id=new_id("intent"),
                    actor_id="test.replay",
                    resource_id=FORECAST_MODEL_RESOURCE_ID,
                    capability_id=RUN_FLOOD_FORECAST,
                    arguments={"input_product_id": input_product.product_id},
                    requested_at=utc_now(),
                ))
                self.assertEqual(CommandState.CONFIRMED, repeated.state)
                self.assertTrue(repeated.output["reused"])
                self.assertEqual(1, len(runner.calls))
                self.assertEqual(2, len(system.runtime.commands()))
                self.assertEqual(2, len(system.runtime.products()))
            finally:
                await system.stop()

    async def test_below_threshold_records_facts_without_running_model(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeForecastRunner()
            system = create_flood_forecast_domain_system(
                rows=evolution_rows(),
                runner=runner,
                artifact_root=Path(directory) / "products",
                evolution_run_id="quiet-run",
            )
            await system.start()
            try:
                await system.advance()

                self.assertEqual([], runner.calls)
                self.assertEqual((), system.runtime.products())
                self.assertEqual((), system.runtime.commands())
                self.assertEqual(
                    (),
                    system.runtime.events(event_type=FORECAST_REQUIRED_EVENT),
                )
            finally:
                await system.stop()

    async def test_model_failure_is_explicit_and_does_not_create_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeForecastRunner(error="model unavailable")
            system = create_flood_forecast_domain_system(
                rows=evolution_rows(peak_by_sequence={24: 240.0}),
                runner=runner,
                artifact_root=Path(directory) / "products",
                evolution_run_id="failed-run",
            )
            await system.start()
            try:
                await system.advance()

                self.assertEqual(
                    1,
                    len(system.runtime.products(product_type=FORECAST_INPUT_PRODUCT)),
                )
                self.assertEqual(
                    (),
                    system.runtime.products(product_type=FORECAST_PRODUCT),
                )
                command = system.runtime.commands()[0]
                self.assertEqual(CommandState.FAILED, command.state)
                self.assertEqual("model unavailable", command.error)
                failed = system.runtime.events(event_type=FORECAST_FAILED_EVENT)
                self.assertEqual(1, len(failed))
                self.assertEqual(command.command_id, failed[0].data["command_id"])
            finally:
                await system.stop()

    async def test_reset_evolution_starts_a_new_immutable_product_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeForecastRunner()
            quiet_rows = evolution_rows()
            storm_rows = evolution_rows(peak_by_sequence={24: 240.0})
            system = create_flood_forecast_domain_system(
                rows=quiet_rows,
                runner=runner,
                artifact_root=Path(directory) / "products",
                evolution_run_id="first-run",
            )
            await system.start()
            try:
                await system.advance()
                await system.advance()
                await system.advance()
                self.assertEqual(
                    2,
                    system.runtime.projection(EVOLUTION_RESOURCE_ID)[
                        "sequence"
                    ].value,
                )

                reset = system.reset_evolution(
                    storm_rows,
                    run_id="second-run",
                    source_ref="playback-source:test",
                )
                await system.advance()

                self.assertEqual("second-run", reset["evolution_run_id"])
                self.assertEqual(0, reset["next_sequence"])
                self.assertTrue(reset["next_step_forecast_triggered"])
                self.assertEqual(2, len(system.runtime.products()))
                self.assertEqual(1, len(runner.calls))
                projection = system.runtime.projection(EVOLUTION_RESOURCE_ID)[
                    "sequence"
                ]
                self.assertEqual(0, projection.value)
                self.assertTrue(
                    projection.observation_id.startswith("second-run:0:")
                )

                await system.runtime.ingest(
                    Observation(
                        observation_id="late-first-run-sequence",
                        resource_id=EVOLUTION_RESOURCE_ID,
                        metric="sequence",
                        value=99,
                        unit=None,
                        observed_at=datetime(
                            2026,
                            8,
                            27,
                            tzinfo=timezone(timedelta(hours=8)),
                        ),
                        received_at=utc_now(),
                        quality=ObservationQuality.GOOD,
                        sequence=99,
                        source_ref="late-test",
                        attributes={"projection_epoch": "first-run"},
                    ),
                    driver_id=EVOLUTION_DRIVER_ID,
                )
                self.assertEqual(
                    0,
                    system.runtime.projection(EVOLUTION_RESOURCE_ID)[
                        "sequence"
                    ].value,
                )
            finally:
                await system.stop()

    async def test_latest_projection_epoch_is_rebuilt_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "epochs.sqlite"
            first_store = SqliteDomainStore(database)
            first = create_flood_forecast_domain_system(
                rows=evolution_rows(),
                store=first_store,
                runner=FakeForecastRunner(),
                artifact_root=root / "products",
                evolution_run_id="persisted-first-run",
            )
            await first.start()
            await first.advance()
            await first.advance()
            await first.advance()
            first.reset_evolution(
                evolution_rows(peak_by_sequence={24: 240.0}),
                run_id="persisted-second-run",
            )
            await first.advance()
            await first.stop()
            first_store.close()

            second_store = SqliteDomainStore(database)
            second = create_flood_forecast_domain_system(
                rows=evolution_rows(),
                store=second_store,
                runner=FakeForecastRunner(),
                artifact_root=root / "products",
                evolution_run_id="unused-after-restore",
            )
            await second.start()
            try:
                projection = second.runtime.projection(
                    EVOLUTION_RESOURCE_ID
                )["sequence"]
                self.assertEqual(0, projection.value)
                self.assertTrue(
                    projection.observation_id.startswith(
                        "persisted-second-run:0:"
                    )
                )
            finally:
                await second.stop()
                second_store.close()

    async def test_products_and_forecast_events_restore_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "domain.sqlite"
            rows = evolution_rows(peak_by_sequence={24: 240.0})
            first_store = SqliteDomainStore(database)
            first = create_flood_forecast_domain_system(
                rows=rows,
                store=first_store,
                runner=FakeForecastRunner(),
                artifact_root=root / "products",
                evolution_run_id="persistent-run",
            )
            await first.start()
            await first.advance()
            product_ids = [item.product_id for item in first.runtime.products()]
            await first.stop()
            first_store.close()

            second_store = SqliteDomainStore(database)
            second = create_flood_forecast_domain_system(
                rows=rows,
                store=second_store,
                runner=FakeForecastRunner(),
                artifact_root=root / "products",
                evolution_run_id="persistent-run",
            )
            await second.start()
            try:
                self.assertEqual(
                    product_ids,
                    [item.product_id for item in second.runtime.products()],
                )
                self.assertEqual(
                    1,
                    len(second.runtime.events(event_type=FORECAST_GENERATED_EVENT)),
                )
                self.assertEqual(
                    CommandState.CONFIRMED,
                    second.runtime.commands()[0].state,
                )
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
