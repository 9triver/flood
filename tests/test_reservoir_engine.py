import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from domains.flood.runtime.reservoir_dispatch import DISPATCH_DATA_DIR
from scripts.reservoir_dispatch import build_parser, dispatch
from domains.flood.runtime.reservoir_engine import (
    OUTPUT_FIELDS,
    ForecastMetrics,
    available_release,
    load_curve,
    load_check_flood_peak,
    load_control_settings,
    load_dispatch_parameters,
    load_hydrograph,
    load_run_parameters,
    load_rule_settings,
    load_safe_release,
    is_super_standard_flood,
    parse_time,
    select_release,
    validate_run_window,
)


BASE_DIR = Path(__file__).resolve().parent / "fixtures" / "reservoir_dispatch"
PARAMETERS = load_dispatch_parameters(DISPATCH_DATA_DIR / "reservoir_level_parameters.dat")
RUN_PARAMETERS = load_run_parameters(DISPATCH_DATA_DIR / "dispatch_run_parameters.dat")
CONTROL_SETTINGS = load_control_settings(DISPATCH_DATA_DIR / "dispatch_control_mode.dat")
RULE_SETTINGS = load_rule_settings(CONTROL_SETTINGS.rule_file)
FLOOD_LIMIT_LEVEL = PARAMETERS.flood_limit_level
CHECK_FLOOD_LEVEL = PARAMETERS.check_flood_level


def metrics(
    *,
    base: float = 245.0,
    next_safe: float | None = None,
    safe: float = 245.0,
    full: float = 245.0,
    required: float = 0.0,
    peak: float = 30.0,
) -> ForecastMetrics:
    return ForecastMetrics(
        peak_inflow=peak,
        max_level_base=base,
        next_level_safe=min(base, FLOOD_LIMIT_LEVEL) if next_safe is None else next_safe,
        max_level_safe=safe,
        max_level_full=full,
        required_release=required,
        extrapolated=False,
    )


class DispatchRulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.storage_curve = load_curve(DISPATCH_DATA_DIR / "storage_capacity_curve.dat")
        cls.outflow_curve = load_curve(DISPATCH_DATA_DIR / "outflow_curve.dat")

    def test_derived_control_values(self) -> None:
        limit_storage, _ = self.storage_curve.value(FLOOD_LIMIT_LEVEL)
        design_storage, _ = self.storage_curve.value(PARAMETERS.design_flood_level)
        check_storage, _ = self.storage_curve.value(CHECK_FLOOD_LEVEL)
        check_release, _ = self.outflow_curve.value(CHECK_FLOOD_LEVEL)
        self.assertAlmostEqual(PARAMETERS.design_flood_level, 247.92, places=2)
        self.assertAlmostEqual(limit_storage, 1972.50, places=2)
        self.assertAlmostEqual(design_storage, 2463.65, places=2)
        self.assertAlmostEqual(check_storage, 2663.36, places=2)
        self.assertAlmostEqual(check_release, 225.74, places=2)

    def test_spillway_release_is_zero_below_crest(self) -> None:
        release, extrapolated = available_release(245.0, self.outflow_curve, 0.0)
        self.assertEqual(release, 0.0)
        self.assertFalse(extrapolated)

    def test_normal_state(self) -> None:
        state, release = select_release(245.0, metrics(), False, 50.0, 5.0, 20.0, PARAMETERS, RULE_SETTINGS)
        self.assertEqual((state, release), (0, 5.0))

    def test_prerelease_state_honors_all_limits(self) -> None:
        state, release = select_release(
            245.0,
            metrics(base=245.4, required=40.0, peak=30.0),
            False,
            25.0,
            0.0,
            20.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (1, 20.0))

    def test_full_capacity_state_when_safe_release_cannot_stop_rise(self) -> None:
        state, release = select_release(
            245.30,
            metrics(base=245.5, next_safe=245.31, safe=245.4, full=245.35),
            False,
            10.0,
            0.0,
            15.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (2, 15.0))

    def test_imminent_crossing_defines_approaching_limit(self) -> None:
        forecast = metrics(base=245.5, next_safe=245.31, safe=245.4, full=245.35)
        state, release = select_release(
            245.25,
            forecast,
            False,
            10.0,
            0.0,
            15.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (2, 15.0))

    def test_distant_second_peak_does_not_mean_continuously_rising(self) -> None:
        state, release = select_release(
            245.6,
            metrics(base=246.0, next_safe=245.59, safe=246.0, full=245.9),
            False,
            4.0,
            0.0,
            8.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (3, 4.0))

    def test_drawdown_state(self) -> None:
        state, release = select_release(
            245.6,
            metrics(base=245.6, safe=245.6, full=245.6),
            False,
            4.0,
            0.0,
            8.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (3, 4.0))

    def test_stable_at_limit_returns_to_normal(self) -> None:
        state, release = select_release(
            FLOOD_LIMIT_LEVEL,
            metrics(base=FLOOD_LIMIT_LEVEL, safe=FLOOD_LIMIT_LEVEL, full=FLOOD_LIMIT_LEVEL),
            False,
            50.0,
            0.0,
            0.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (0, 0.0))

    def test_emergency_flag_overrides_other_rules(self) -> None:
        state, release = select_release(245.0, metrics(), True, 5.0, 0.0, 20.0, PARAMETERS, RULE_SETTINGS)
        self.assertEqual((state, release), (4, 20.0))

    def test_check_level_forecast_triggers_emergency(self) -> None:
        state, release = select_release(
            248.0,
            metrics(base=249.5, safe=249.4, full=CHECK_FLOOD_LEVEL + 0.01),
            False,
            50.0,
            0.0,
            200.0,
            PARAMETERS,
            RULE_SETTINGS,
        )
        self.assertEqual((state, release), (4, 200.0))

    def test_external_safe_release_file(self) -> None:
        self.assertEqual(load_safe_release(DISPATCH_DATA_DIR / "downstream_safe_release.dat"), 30.0)

    def test_external_check_peak_and_automatic_super_standard_judgement(self) -> None:
        check_peak = load_check_flood_peak(DISPATCH_DATA_DIR / "check_flood_peak_inflow.dat")
        self.assertAlmostEqual(check_peak, 679.0, places=2)
        self.assertFalse(is_super_standard_flood(check_peak, check_peak))
        self.assertTrue(is_super_standard_flood(check_peak + 0.01, check_peak))

    def test_formal_input_ignores_comment_lines(self) -> None:
        rows = load_hydrograph(BASE_DIR / "dispatch_input.csv")
        self.assertEqual(len(rows), 49)
        self.assertEqual(max(row.inflow for row in rows), 900.0)
        validate_run_window(rows, RUN_PARAMETERS, RUN_PARAMETERS.time_step_hours)

    def test_external_run_parameters(self) -> None:
        self.assertEqual(str(RUN_PARAMETERS.calculation_start_time), "2026-07-21 00:00:00")
        self.assertEqual(str(RUN_PARAMETERS.calculation_end_time), "2026-07-23 01:00:00")
        self.assertEqual(RUN_PARAMETERS.total_periods, 49)
        self.assertEqual(RUN_PARAMETERS.time_step_hours, 1.0)
        self.assertAlmostEqual(RUN_PARAMETERS.initial_level, 245.10, places=2)

    def test_run_window_rejects_wrong_period_count(self) -> None:
        rows = load_hydrograph(BASE_DIR / "dispatch_input.csv")
        with self.assertRaisesRegex(ValueError, "does not match TOTAL_PERIODS"):
            validate_run_window(rows[:-1], RUN_PARAMETERS, RUN_PARAMETERS.time_step_hours)

    def test_time_format_rejects_t_separator(self) -> None:
        with self.assertRaisesRegex(ValueError, "without 'T'"):
            parse_time("2026-07-21T00:00:00")

    def test_input_rejects_super_standard_column(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.csv"
            path.write_text(
                "time,inflow_m3s,super_standard\n"
                "2026-07-21 00:00:00,10,0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must not contain super_standard"):
                load_hydrograph(path)

    def test_output_excludes_run_and_external_input_parameters(self) -> None:
        excluded = {
            "calculation_start_time",
            "calculation_end_time",
            "total_periods",
            "time_step_hours",
            "initial_level_m",
            "check_flood_peak_inflow_m3s",
            "input_peak_inflow_m3s",
            "super_standard_flood",
            "super_standard_reason",
            "outflow_control_error_m3s",
            "level_control_error_m",
            "flood_limit_level_m",
            "design_flood_level_m",
            "check_flood_level_m",
            "state_code",
        }
        self.assertTrue(excluded.isdisjoint(OUTPUT_FIELDS))

    def test_external_control_and_rule_files(self) -> None:
        self.assertEqual(CONTROL_SETTINGS.mode, "RULE")
        self.assertEqual(CONTROL_SETTINGS.rule_file.name, "dispatch_rules.dat")
        self.assertTrue(RULE_SETTINGS.emergency_override_manual)

    def run_example(self, directory: str, control_file: str, *extra: str):
        root = Path(directory)
        output = root / "output.csv"
        control = root / "control.dat"
        mode = "OUTFLOW" if "Outflow" in control_file else "LEVEL"
        control.write_text(
            f"CONTROL_MODE = {mode}\nRULE_FILE = {CONTROL_SETTINGS.rule_file}\n",
            encoding="utf-8",
        )
        example = root / "manual_input.csv"
        with (BASE_DIR / "dispatch_input.csv").open(newline="") as source:
            reader = csv.DictReader(source)
            with example.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=reader.fieldnames)
                writer.writeheader()
                for row in reader:
                    writer.writerow({**row, "target_outflow_m3s": 15, "target_level_m": 245.30})
        args = build_parser().parse_args([str(example), str(output), "--control-file", str(control), *extra])
        self.assertEqual(dispatch(args), 0)
        with output.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_outflow_control_tracks_input_target(self) -> None:
        with TemporaryDirectory() as directory:
            rows = self.run_example(
                directory,
                "Dispatch_Control_Outflow_Example.DAT",
                "--check-flood-peak",
                "1000",
                "--outlet-capacity",
                "300",
            )
        self.assertEqual(len(rows), 49)
        self.assertEqual(rows[0]["state"], "OUTFLOW_CONTROL")
        self.assertEqual(rows[0]["control_status"], "TARGET_MET")
        self.assertEqual(rows[0]["release_m3s"], "15.000")

    def test_level_control_uses_end_of_period_target(self) -> None:
        with TemporaryDirectory() as directory:
            rows = self.run_example(
                directory,
                "Dispatch_Control_Level_Example.DAT",
                "--check-flood-peak",
                "1000",
                "--outlet-capacity",
                "300",
            )
        self.assertEqual(len(rows), 49)
        self.assertEqual(rows[0]["state"], "LEVEL_CONTROL")
        self.assertEqual(rows[0]["control_status"], "TARGET_REQUIRES_STORAGE_INCREASE")
        self.assertEqual(rows[0]["release_m3s"], "0.000")
        self.assertAlmostEqual(float(rows[0]["end_level_m"]), 245.203, places=3)

    def test_super_standard_flood_overrides_manual_control(self) -> None:
        with TemporaryDirectory() as directory:
            rows = self.run_example(
                directory,
                "Dispatch_Control_Outflow_Example.DAT",
                "--outlet-capacity",
                "20",
            )
        self.assertEqual(len(rows), 49)
        self.assertTrue(all(row["state"] == "EMERGENCY" for row in rows))
        self.assertTrue(all(row["control_status"] == "EMERGENCY_OVERRIDE" for row in rows))

    def test_manual_mode_requires_its_target_column(self) -> None:
        with TemporaryDirectory() as directory:
            control = Path(directory) / "control.dat"
            control.write_text(
                "CONTROL_MODE = OUTFLOW\nRULE_FILE = "
                f"{DISPATCH_DATA_DIR / 'dispatch_rules.dat'}\n",
                encoding="utf-8",
            )
            parser = build_parser()
            args = parser.parse_args(
                [
                    str(BASE_DIR / "dispatch_input.csv"),
                    str(Path(directory) / "output.csv"),
                    "--control-file",
                    str(control),
                ]
            )
            with self.assertRaisesRegex(ValueError, "OUTFLOW mode requires target_outflow_m3s"):
                dispatch(args)


if __name__ == "__main__":
    unittest.main()
