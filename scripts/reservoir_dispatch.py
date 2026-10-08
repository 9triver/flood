#!/usr/bin/env python3
"""Run the migrated reservoir engine against a standalone CSV hydrograph."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from domains.flood.runtime.reservoir_dispatch import DISPATCH_DATA_DIR
from domains.flood.runtime.reservoir_engine import (
    OUTPUT_FIELDS,
    available_release,
    calculate_dispatch,
    interval_seconds,
    load_check_flood_peak,
    load_control_settings,
    load_curve,
    load_dispatch_parameters,
    load_hydrograph,
    load_rule_settings,
    load_run_parameters,
    load_safe_release,
    validate_control_inputs,
    validate_non_negative,
    validate_run_window,
)


def dispatch(args: argparse.Namespace) -> int:
    if args.forecast_steps is not None and args.forecast_steps <= 0:
        raise ValueError("--forecast-steps must be a positive integer")

    base_dir = DISPATCH_DATA_DIR
    control_mode_path = args.control_file or base_dir / "dispatch_control_mode.dat"
    control_settings = load_control_settings(control_mode_path)
    rule_settings = load_rule_settings(control_settings.rule_file)
    run_parameter_path = args.run_parameters or base_dir / "dispatch_run_parameters.dat"
    run_parameters = load_run_parameters(run_parameter_path)
    initial_level = (
        args.initial_level
        if args.initial_level is not None
        else run_parameters.initial_level
    )
    time_step_hours = (
        args.step_hours
        if args.step_hours is not None
        else run_parameters.time_step_hours
    )
    level_parameter_path = args.level_parameters or base_dir / "reservoir_level_parameters.dat"
    safe_release_path = args.safe_release_file or base_dir / "downstream_safe_release.dat"
    check_flood_peak_path = (
        args.check_flood_peak_file or base_dir / "check_flood_peak_inflow.dat"
    )
    parameters = load_dispatch_parameters(level_parameter_path)
    safe_release = (
        args.safe_release
        if args.safe_release is not None
        else load_safe_release(safe_release_path)
    )
    check_flood_peak = (
        args.check_flood_peak
        if args.check_flood_peak is not None
        else load_check_flood_peak(check_flood_peak_path)
    )
    if check_flood_peak <= 0:
        raise ValueError("--check-flood-peak must be positive")
    for name, value in (
        ("initial-level", initial_level),
        ("safe-release", safe_release),
        ("check-flood-peak", check_flood_peak),
        ("normal-release", args.normal_release),
        ("outlet-capacity", args.outlet_capacity),
    ):
        validate_non_negative(f"--{name}", value)

    storage_curve = load_curve(base_dir / "storage_capacity_curve.dat")
    outflow_curve = load_curve(base_dir / "outflow_curve.dat")
    rows = load_hydrograph(args.input)
    validate_control_inputs(rows, control_settings)
    steps = interval_seconds(rows, time_step_hours)
    validate_run_window(rows, run_parameters, time_step_hours)
    results = calculate_dispatch(
        rows, steps, initial_level=initial_level, storage_curve=storage_curve, outflow_curve=outflow_curve,
        parameters=parameters, rules=rule_settings, mode=control_settings.mode, safe_release=safe_release,
        check_flood_peak=check_flood_peak, normal_release=args.normal_release,
        outlet_capacity=args.outlet_capacity, forecast_steps=args.forecast_steps,
        max_release=getattr(args, "max_release", None),
    )
    design_release = round(available_release(parameters.design_flood_level, outflow_curve, 0)[0], 2)
    if getattr(args, "max_release", None) is not None and args.max_release > design_release:
        print(f"提醒：设定的最大下泄流量 {args.max_release:.2f} m³/s 大于设计下泄参考值 {design_release:.2f} m³/s；本次仍按用户设定的上限计算。", file=sys.stderr)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows({key: f"{value:.3f}" if isinstance(value, float) else value
                          for key, value in row.items()} for row in results)

    if any(row["curve_extrapolated"] for row in results):
        print(
            "Warning: one or more values were outside a supplied curve and were linearly extrapolated.",
            file=sys.stderr,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the numerical Longtan reservoir flood-dispatch rules."
    )
    parser.add_argument(
        "input",
        type=Path,
        help=(
            "Formal CSV with time,inflow_m3s and optional "
            "target_outflow_m3s,target_level_m control columns"
        ),
    )
    parser.add_argument("output", type=Path, help="Output dispatch CSV")
    parser.add_argument(
        "--control-file",
        type=Path,
        help="External RULE/OUTFLOW/LEVEL control-mode DAT file (default: domains/flood/data/dispatch/dispatch_control_mode.dat)",
    )
    parser.add_argument(
        "--run-parameters",
        type=Path,
        help="External run-window/initial-condition DAT file (default: domains/flood/data/dispatch/dispatch_run_parameters.dat)",
    )
    parser.add_argument(
        "--initial-level",
        type=float,
        help="Temporary initial-level override; otherwise read the run-parameter DAT file",
    )
    parser.add_argument(
        "--safe-release",
        type=float,
        help="Temporary override; otherwise read the external safe-release DAT file",
    )
    parser.add_argument(
        "--safe-release-file",
        type=Path,
        help="External downstream safe-release DAT file (default: domains/flood/data/dispatch/downstream_safe_release.dat)",
    )
    parser.add_argument(
        "--level-parameters",
        type=Path,
        help="External flood/design/check level DAT file (default: domains/flood/data/dispatch/reservoir_level_parameters.dat)",
    )
    parser.add_argument(
        "--check-flood-peak",
        type=float,
        help="Temporary check-flood peak inflow override in m3/s",
    )
    parser.add_argument(
        "--check-flood-peak-file",
        type=Path,
        help="External check-flood peak inflow DAT file (default: domains/flood/data/dispatch/check_flood_peak_inflow.dat)",
    )
    parser.add_argument(
        "--normal-release",
        type=float,
        default=0.0,
        help="Normal-operation release in m3/s (default: 0)",
    )
    parser.add_argument(
        "--outlet-capacity",
        type=float,
        default=0.0,
        help="Confirmed separate outlet capacity not included in outflow curve (default: 0)",
    )
    parser.add_argument("--max-release", type=float,
                        help="User-selected total release upper limit; overrides curve capacity and safe-release limit, including emergencies")
    parser.add_argument(
        "--forecast-steps",
        type=int,
        help="Forecast look-ahead rows (default: all remaining rows)",
    )
    parser.add_argument(
        "--step-hours",
        type=float,
        help="Temporary time-step override; otherwise read the run-parameter DAT file",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
