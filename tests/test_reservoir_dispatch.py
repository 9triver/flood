from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from domains.flood.runtime import reservoir_engine as engine
from domains.flood.runtime.boundary_flow import (
    BoundaryFlowPlaybackSource, FloodForecastPolicy, load_boundary_flow_rows,
)
from domains.flood.runtime.playback_sources import (
    PlaybackSourceValidationError, validate_playback_source,
)
from domains.flood.runtime.reservoir_dispatch import (
    DISPATCH_DATA_DIR, MODEL_SOURCE, DispatchParameters, DispatchSettings,
    parse_dispatch_settings, simulate_reservoir_dispatch,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reservoir_dispatch"


def rainfall_input(targets=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["time_period_end", "interval1_rainfall_mm", "interval2_rainfall_mm", "reservoir_rainfall_mm",
                     *(["target_outflow_m3s", "target_level_m"] if targets is not None else [])])
    for index in range(25):
        writer.writerow([(datetime(2026, 7, 1) + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M"),
                         1 if index == 1 else 0, 2 if index == 1 else 0, 10 if index == 1 else 0,
                         *(targets if targets is not None else [])])
    return stream.getvalue().encode()



class ReservoirDispatchIntegrationTest(unittest.TestCase):
    def test_continuation_preserves_storage_rules_and_original_process_peak(self):
        for mode, target in (
            ("RULE", {}), ("OUTFLOW", {"target_outflow_m3s": 20}),
            ("LEVEL", {"target_level_m": 247}),
        ):
            settings = DispatchSettings(mode=mode, initial_level_m=247, **target)
            inflows = [900] + [10] * 49
            full = simulate_reservoir_dispatch(inflows, settings=settings)["series"]
            for cut in (2, 30):
                with self.subTest(mode=mode, cut=cut):
                    state = full[cut]["continuation_state"]
                    tail = simulate_reservoir_dispatch(
                        inflows[cut + 1:], settings=settings,
                        initial_storage_1e4m3=state["storage_1e4m3"],
                        initial_level_m=state["level_m"],
                        input_peak_inflow_m3s=state["input_peak_inflow_m3s"],
                    )["series"]
                    for expected, actual in zip(full[cut + 1:], tail):
                        for key in ("release_m3s", "end_level_m", "end_storage_1e4m3", "state", "control_status"):
                            self.assertEqual(expected[key], actual[key])

    def test_future_peak_triggers_emergency_outside_short_forecast_window(self):
        for mode, target in (
            ("RULE", {}),
            ("OUTFLOW", {"target_outflow_m3s": 0}),
            ("LEVEL", {"target_level_m": 246.5}),
        ):
            with self.subTest(mode=mode):
                result = simulate_reservoir_dispatch(
                    [0, 0, 900, 0],
                    settings=DispatchSettings(
                        mode=mode, initial_level_m=246.5, forecast_steps=1, **target,
                    ),
                )
                self.assertTrue(result["super_standard_flood"])
                self.assertEqual(result["series"][0]["forecast_peak_inflow_m3s"], 0)
                self.assertTrue(all(point["state"] == "EMERGENCY" for point in result["series"]))
                self.assertGreater(result["series"][0]["release_m3s"], 30)

    def test_stored_water_below_flood_limit_is_available_for_manual_release(self):
        point = simulate_reservoir_dispatch(
            [0], dt_hours=0.5,
            settings=DispatchSettings(
                mode="OUTFLOW", initial_level_m=245.10,
                target_outflow_m3s=10, outlet_capacity_m3s=20,
            ),
        )["series"][0]
        self.assertEqual(point["release_m3s"], 10)
        self.assertEqual(point["control_status"], "TARGET_MET")
        self.assertLess(point["end_level_m"], 245.10)
        self.assertAlmostEqual(
            point["start_storage_1e4m3"] - point["end_storage_1e4m3"], 1.8,
        )

    def test_per_period_targets_override_settings_and_blank_targets_fall_back(self):
        result = simulate_reservoir_dispatch(
            [
                {"valid_time": "2026-10-07T10:00:00+08:00", "inflow_m3s": 10, "target_outflow_m3s": 0},
                {"valid_time": "2026-10-07T10:30:00+08:00", "inflow_m3s": 10, "target_outflow_m3s": 15},
                {"valid_time": "2026-10-07T11:00:00+08:00", "inflow_m3s": 10, "target_outflow_m3s": " "},
            ],
            dt_hours=0.5,
            settings=DispatchSettings(mode="OUTFLOW", initial_level_m=246.5, target_outflow_m3s=10),
        )
        self.assertEqual([point["release_m3s"] for point in result["series"]], [0, 15, 10])
        self.assertEqual(result["series"][0]["valid_time"], "2026-10-07T10:00:00+08:00")
        self.assertEqual(result["settings"]["model_source"], MODEL_SOURCE)
        for point in result["series"]:
            self.assertAlmostEqual(
                point["end_storage_1e4m3"],
                point["start_storage_1e4m3"] + (10 - point["release_m3s"]) * 1800 / 10000,
                places=5,
            )

    def test_missing_and_invalid_manual_targets_are_rejected(self):
        for mode, field in (("OUTFLOW", "target_outflow_m3s"), ("LEVEL", "target_level_m")):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, field):
                simulate_reservoir_dispatch([10], settings=DispatchSettings(mode=mode))
            for value in (-1, float("nan"), float("inf")):
                with self.subTest(mode=mode, value=value), self.assertRaises(ValueError):
                    simulate_reservoir_dispatch(
                        [{"inflow_m3s": 10, field: value}], settings=DispatchSettings(mode=mode),
                    )

    def test_legacy_parameter_overrides_remain_supported(self):
        parameters = DispatchParameters(initial_level_m=246.5, downstream_safe_release_m3s=12)
        result = simulate_reservoir_dispatch(
            [0], parameters=parameters,
            settings=DispatchSettings(mode="OUTFLOW", target_outflow_m3s=20),
        )
        point = result["series"][0]
        self.assertEqual(point["start_level_m"], 246.5)
        self.assertEqual(point["release_m3s"], 12)
        self.assertEqual(point["control_status"], "LIMITED_BY_SAFE_RELEASE")

    def test_empty_series_and_invalid_steps(self):
        result = simulate_reservoir_dispatch([], initial_level_m=246.5)
        self.assertEqual(result["series"], [])
        self.assertEqual(result["max_level_m"], 246.5)
        self.assertFalse(result["super_standard_flood"])
        for step in (0, -1, float("nan"), float("inf")):
            with self.subTest(step=step), self.assertRaises(ValueError):
                simulate_reservoir_dispatch([10], dt_hours=step)

    def test_shared_engine_matches_supplied_reference_output(self):
        inputs = engine.load_hydrograph(FIXTURES / "dispatch_input.csv")
        actual = simulate_reservoir_dispatch(
            [{"valid_time": row.time, "inflow_m3s": row.inflow} for row in inputs],
            settings=DispatchSettings(outlet_capacity_m3s=20, forecast_steps=len(inputs)),
        )["series"]
        with (FIXTURES / "dispatch_output.csv").open(encoding="utf-8-sig", newline="") as handle:
            reference = list(csv.DictReader(handle))
        self.assertEqual(len(reference), len(actual))
        for expected, point in zip(reference, actual):
            self.assertEqual(expected["state"], point["state"])
            self.assertEqual(expected["control_status"], point["control_status"])
            for key in ("release_m3s", "end_level_m", "end_storage_1e4m3", "forecast_max_level_full_m"):
                self.assertAlmostEqual(float(expected[key]), point[key], delta=0.0011)


    def test_outflow_control_tracks_target_and_preserves_water_balance(self):
        points = simulate_reservoir_dispatch([10] * 5, settings=DispatchSettings(
            mode="OUTFLOW", initial_level_m=246.5, target_outflow_m3s=10,
        ))["series"]
        for point in points:
            self.assertEqual(point["control_status"], "TARGET_MET")
            self.assertEqual(point["release_m3s"], 10)
            self.assertEqual(point["end_level_m"], 246.5)
            self.assertAlmostEqual(point["end_storage_1e4m3"], point["start_storage_1e4m3"]
                                   + (point["inflow_m3s"] - point["release_m3s"]) * 3600 / 10000, places=5)


    def test_level_control_uses_end_level_target(self):
        point = simulate_reservoir_dispatch([10], settings=DispatchSettings(
            mode="LEVEL", initial_level_m=246.5, target_level_m=246.5,
        ))["series"][0]
        self.assertEqual(point["state"], "LEVEL_CONTROL")
        self.assertEqual(point["control_status"], "TARGET_MET")
        self.assertEqual(point["release_m3s"], 10)
        self.assertEqual(point["end_level_m"], 246.5)


    def test_manual_targets_are_limited_and_emergency_can_override(self):
        settings = DispatchSettings(mode="OUTFLOW", initial_level_m=246.5, target_outflow_m3s=100)
        point = simulate_reservoir_dispatch([10], settings=settings)["series"][0]
        self.assertEqual(point["release_m3s"], 30)
        self.assertEqual(point["control_status"], "LIMITED_BY_SAFE_RELEASE")
        point = simulate_reservoir_dispatch([10], settings=DispatchSettings(
            mode="OUTFLOW", target_outflow_m3s=10,
        ))["series"][0]
        self.assertEqual(point["release_m3s"], 0)
        self.assertEqual(point["control_status"], "LIMITED_BY_CAPACITY")
        point = simulate_reservoir_dispatch([900], settings=settings)["series"][0]
        self.assertEqual(point["state"], "EMERGENCY")
        self.assertEqual(point["control_status"], "EMERGENCY_OVERRIDE")
        self.assertGreater(point["release_m3s"], 30)


    def test_every_boundary_and_forecast_use_the_calculated_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rainfall.csv"
            path.write_bytes(rainfall_input([10, ""]))
            config = DispatchSettings(mode="OUTFLOW", initial_level_m=246.5)
            source = BoundaryFlowPlaybackSource(path, Path(directory) / "observations.jsonl", dispatch_settings=config)
            rule_rows = load_boundary_flow_rows(path)
            for row, rule in zip(source.rows, rule_rows):
                self.assertEqual(row["reservoir_inflow_m3s"], rule["reservoir_inflow_m3s"])
                self.assertEqual(row["boundaries"]["interval1"], rule["boundaries"]["interval1"])
                self.assertEqual(row["boundaries"]["interval2"], rule["boundaries"]["interval2"])
                self.assertEqual(row["boundaries"]["upstream"]["flow_m3s"], row["reservoir_dispatch"]["release_m3s"])
                self.assertEqual(row["reservoir_outlet_flow_m3s"], row["reservoir_release_m3s"])
            observation = source.next_observation()
            for point, row in zip(observation["reservoir_forecast"]["series"], source.rows[1:]):
                self.assertEqual(point["reservoir_release_m3s"], row["boundaries"]["upstream"]["flow_m3s"])
            policy = FloodForecastPolicy(source.rows, total_trigger_m3s=1,
                                        forecast_input_dir=Path(directory) / "inputs", latest_forecast_input_path=Path(directory) / "latest.json")
            policy.observe(observation)
            snapshot = policy.latest_forecast_input
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["summary"]["reservoir_dispatch_settings"]["mode"], "OUTFLOW")
            boundary = snapshot["summary"]["boundaries"]["upstream"]["series"]
            self.assertEqual([point["flow_m3s"] for point in boundary], [row["reservoir_release_m3s"] for row in source.rows])


    def test_user_max_above_design_controls_all_modes_and_warns(self):
        for mode, target in (("RULE", {}), ("OUTFLOW", {"target_outflow_m3s": 250}),
                             ("LEVEL", {"target_level_m": 246.5})):
            with self.subTest(mode=mode):
                result = simulate_reservoir_dispatch([250], settings=DispatchSettings(
                    mode=mode, initial_level_m=246.5, max_release_m3s=300, **target))
                point = result["series"][0]
                self.assertGreater(point["release_m3s"], point["design_available_release_m3s"])
                self.assertGreater(point["release_m3s"], 139.59)
                self.assertLessEqual(point["release_m3s"], 300)
                self.assertEqual(point["available_release_m3s"], 300)
                self.assertEqual(result["warnings"][0]["code"], "max_release_exceeds_design")
                self.assertEqual(result["warnings"][0]["design_release_m3s"], 139.59)
                self.assertAlmostEqual(point["end_storage_1e4m3"], point["start_storage_1e4m3"]
                                       + (250 - point["release_m3s"]) * 3600 / 10000, places=5)
        point = simulate_reservoir_dispatch([250], settings=DispatchSettings(
            mode="OUTFLOW", initial_level_m=246.5, max_release_m3s=300, target_outflow_m3s=500,
        ))["series"][0]
        self.assertEqual(point["release_m3s"], 300)
        self.assertEqual(point["control_status"], "LIMITED_BY_USER_MAX_RELEASE")


    def test_user_max_remains_an_upper_limit_during_emergencies_and_water_shortage(self):
        for mode, target in (("RULE", {}), ("OUTFLOW", {"target_outflow_m3s": 50}),
                             ("LEVEL", {"target_level_m": 246.5})):
            points = simulate_reservoir_dispatch([900, 800], settings=DispatchSettings(
                mode=mode, initial_level_m=246.5, max_release_m3s=300, **target))["series"]
            self.assertTrue(all(point["state"] == "EMERGENCY" for point in points))
            self.assertTrue(all(point["release_m3s"] == 300 for point in points))
        dry = simulate_reservoir_dispatch([0], settings=DispatchSettings(max_release_m3s=300))["series"][0]
        self.assertEqual(dry["release_m3s"], 0)
        minimum = engine.load_curve(DISPATCH_DATA_DIR / "storage_capacity_curve.dat")
        empty = simulate_reservoir_dispatch([1], settings=DispatchSettings(mode="OUTFLOW",
            initial_level_m=minimum.x[0], max_release_m3s=300, target_outflow_m3s=300))["series"][0]
        self.assertEqual(empty["release_m3s"], 1)
        self.assertEqual(empty["control_status"], "LIMITED_BY_WATER_AVAILABLE")
        self.assertEqual(empty["end_storage_1e4m3"], minimum.y[0])


    def test_max_warning_threshold_validation_and_downstream_boundary(self):
        for maximum in (-1, float("nan"), float("inf"), True, "300"):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                parse_dispatch_settings({"max_release_m3s": maximum})
        for maximum in (None, 0, 139.59):
            self.assertEqual(simulate_reservoir_dispatch([0], settings=DispatchSettings(
                max_release_m3s=maximum))["warnings"], [])
        rows = load_boundary_flow_rows(dispatch_settings=DispatchSettings(max_release_m3s=300))
        self.assertEqual(max(row["reservoir_release_m3s"] for row in rows), 300)
        self.assertTrue(all(row["reservoir_release_m3s"] <= 300 for row in rows))
        self.assertTrue(all(row["boundaries"]["upstream"]["flow_m3s"] == row["reservoir_release_m3s"] for row in rows))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = BoundaryFlowPlaybackSource(observation_path=root / "observations.jsonl",
                                                dispatch_settings=DispatchSettings(max_release_m3s=300))
            policy = FloodForecastPolicy(source.rows, total_trigger_m3s=1,
                                        forecast_input_dir=root / "inputs", latest_forecast_input_path=root / "latest.json")
            policy.observe(source.next_observation())
            snapshot = policy.latest_forecast_input
            self.assertEqual(snapshot["summary"]["reservoir_dispatch_settings"]["max_release_m3s"], 300)
            upstream = snapshot["summary"]["boundaries"]["upstream"]["series"]
            self.assertTrue(all(point["flow_m3s"] <= 300 for point in upstream))
            self.assertEqual(upstream[0]["flow_m3s"], source.rows[0]["reservoir_release_m3s"])


    def test_optional_control_columns_are_validated(self):
        validate_playback_source(rainfall_input([10, ""]))
        validate_playback_source(rainfall_input(["", 245.3]))
        for bad in (-1, "nan", "inf", "bad"):
            with self.subTest(target=bad), self.assertRaises(PlaybackSourceValidationError):
                validate_playback_source(rainfall_input([bad, ""]))
