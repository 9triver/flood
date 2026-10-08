"""Longtan reservoir dispatch engine migrated from Scheduling model.

Source: flood-updates-20261006-194945/flood/Scheduling model/reservoir_dispatch.py.
The numerical rules are preserved; see ../data/dispatch/README.md for provenance
and business semantics. The application adapter supplies inputs and parameters;
the standalone CSV command lives in scripts/reservoir_dispatch.py.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence


VOLUME_FACTOR = 10000.0
LEVEL_TOLERANCE = 1e-6
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

STATE_NAMES = {
    0: "NORMAL",
    1: "PRERELEASE",
    2: "FULL_CAPACITY",
    3: "DRAWDOWN",
    4: "EMERGENCY",
    5: "OUTFLOW_CONTROL",
    6: "LEVEL_CONTROL",
}

OUTPUT_FIELDS = [
    "time",
    "inflow_m3s",
    "state",
    "control_status",
    "start_level_m",
    "start_storage_1e4m3",
    "forecast_peak_m3s",
    "forecast_max_level_base_m",
    "forecast_next_level_safe_m",
    "forecast_max_level_safe_m",
    "forecast_max_level_full_m",
    "design_level_exceeded",
    "required_release_m3s",
    "available_release_m3s",
    "design_available_release_m3s",
    "max_release_m3s",
    "release_m3s",
    "end_storage_1e4m3",
    "end_level_m",
    "curve_extrapolated",
]


@dataclass(frozen=True)
class Curve:
    x: tuple[float, ...]
    y: tuple[float, ...]

    def value(self, value: float) -> tuple[float, bool]:
        """Return a linearly interpolated value and an extrapolation flag."""
        if value <= self.x[0]:
            index = 0
            extrapolated = value < self.x[0]
        elif value >= self.x[-1]:
            index = len(self.x) - 2
            extrapolated = value > self.x[-1]
        else:
            lo = 0
            hi = len(self.x) - 1
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if self.x[mid] <= value:
                    lo = mid
                else:
                    hi = mid
            index = lo
            extrapolated = False

        x0, x1 = self.x[index], self.x[index + 1]
        y0, y1 = self.y[index], self.y[index + 1]
        result = y0 + (value - x0) * (y1 - y0) / (x1 - x0)
        return result, extrapolated

    def inverse(self) -> "Curve":
        return Curve(self.y, self.x)


@dataclass(frozen=True)
class HydrographRow:
    time: str
    inflow: float
    target_outflow: float | None
    target_level: float | None


@dataclass(frozen=True)
class DispatchParameters:
    flood_limit_level: float
    design_flood_level: float
    check_flood_level: float
    dam_top_elevation: float = 253.30


@dataclass(frozen=True)
class RunParameters:
    calculation_start_time: datetime
    calculation_end_time: datetime
    total_periods: int
    time_step_hours: float
    initial_level: float


@dataclass(frozen=True)
class ControlSettings:
    mode: str
    rule_file: Path


@dataclass(frozen=True)
class RuleSettings:
    emergency_condition: str
    emergency_release: str
    full_capacity_condition: str
    full_capacity_release: str
    drawdown_condition: str
    drawdown_release: str
    prerelease_condition: str
    prerelease_release: str
    normal_release: str
    manual_apply_safe_release_limit: bool
    emergency_override_manual: bool


@dataclass(frozen=True)
class ForecastMetrics:
    peak_inflow: float
    max_level_base: float
    next_level_safe: float
    max_level_safe: float
    max_level_full: float
    required_release: float
    extrapolated: bool


def parse_time(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), DATETIME_FORMAT)
    except ValueError as error:
        raise ValueError(
            f"Time must use YYYY-MM-DD HH:MM:SS without 'T': {value}"
        ) from error


def format_time(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


def load_numeric_parameters(path: Path) -> dict[str, float]:
    """Read KEY = NUMBER entries from an external DAT parameter file."""
    values: dict[str, float] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, raw_value = (part.strip() for part in line.split("=", 1))
            if not key:
                raise ValueError(f"Missing parameter name at {path}:{line_number}")
            try:
                value = float(raw_value)
            except ValueError as error:
                raise ValueError(
                    f"Parameter {key} at {path}:{line_number} must be numeric"
                ) from error
            if not math.isfinite(value):
                raise ValueError(f"Parameter {key} at {path}:{line_number} is not finite")
            values[key] = value
    if not values:
        raise ValueError(f"No numeric parameters found in {path}")
    return values


def load_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line or line.startswith("["):
                continue
            if "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if not key or not value:
                raise ValueError(f"Invalid parameter at {path}:{line_number}")
            values[key] = value
    if not values:
        raise ValueError(f"No parameters found in {path}")
    return values


def parse_binary_parameter(values: dict[str, str], key: str, path: Path) -> bool:
    if key not in values or values[key] not in {"0", "1"}:
        raise ValueError(f"{key} in {path} must be 0 or 1")
    return values[key] == "1"


def load_control_settings(path: Path) -> ControlSettings:
    values = load_key_values(path)
    mode = values.get("CONTROL_MODE", "").upper()
    if mode not in {"RULE", "OUTFLOW", "LEVEL"}:
        raise ValueError(f"CONTROL_MODE in {path} must be RULE, OUTFLOW, or LEVEL")
    rule_file = values.get("RULE_FILE")
    if not rule_file:
        raise ValueError(f"Missing RULE_FILE in {path}")
    return ControlSettings(mode=mode, rule_file=path.parent / rule_file)


def load_rule_settings(path: Path) -> RuleSettings:
    values = load_key_values(path)
    required = (
        "EMERGENCY_CONDITION",
        "EMERGENCY_RELEASE",
        "FULL_CAPACITY_CONDITION",
        "FULL_CAPACITY_RELEASE",
        "DRAWDOWN_CONDITION",
        "DRAWDOWN_RELEASE",
        "PRERELEASE_CONDITION",
        "PRERELEASE_RELEASE",
        "NORMAL_RELEASE",
        "MANUAL_APPLY_SAFE_RELEASE_LIMIT",
        "EMERGENCY_OVERRIDE_MANUAL",
    )
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"Missing executable rules in {path}: {', '.join(missing)}")
    supported = {
        "EMERGENCY_CONDITION": "SUPER_STANDARD_OR_FULL_FORECAST_ABOVE_CHECK",
        "EMERGENCY_RELEASE": "AVAILABLE_CAPACITY",
        "FULL_CAPACITY_CONDITION": "IMMINENT_LIMIT_CROSSING_OR_RISING_ABOVE_LIMIT",
        "FULL_CAPACITY_RELEASE": "AVAILABLE_CAPACITY",
        "DRAWDOWN_CONDITION": "ABOVE_LIMIT_NOT_RISING",
        "DRAWDOWN_RELEASE": "MIN_CAPACITY_SAFE",
        "PRERELEASE_CONDITION": "BELOW_LIMIT_BASE_FORECAST_ABOVE_LIMIT",
        "PRERELEASE_RELEASE": "MIN_REQUIRED_PEAK_SAFE_CAPACITY",
        "NORMAL_RELEASE": "MIN_NORMAL_SAFE_CAPACITY",
    }
    for key, expected in supported.items():
        if values[key] != expected:
            raise ValueError(f"Unsupported {key}={values[key]} in {path}")
    return RuleSettings(
        emergency_condition=values["EMERGENCY_CONDITION"],
        emergency_release=values["EMERGENCY_RELEASE"],
        full_capacity_condition=values["FULL_CAPACITY_CONDITION"],
        full_capacity_release=values["FULL_CAPACITY_RELEASE"],
        drawdown_condition=values["DRAWDOWN_CONDITION"],
        drawdown_release=values["DRAWDOWN_RELEASE"],
        prerelease_condition=values["PRERELEASE_CONDITION"],
        prerelease_release=values["PRERELEASE_RELEASE"],
        normal_release=values["NORMAL_RELEASE"],
        manual_apply_safe_release_limit=parse_binary_parameter(
            values, "MANUAL_APPLY_SAFE_RELEASE_LIMIT", path
        ),
        emergency_override_manual=parse_binary_parameter(
            values, "EMERGENCY_OVERRIDE_MANUAL", path
        ),
    )


def load_dispatch_parameters(path: Path) -> DispatchParameters:
    values = load_numeric_parameters(path)
    required = (
        "FLOOD_LIMIT_LEVEL_M",
        "DESIGN_FLOOD_LEVEL_M",
        "CHECK_FLOOD_LEVEL_M",
    )
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"Missing level parameters in {path}: {', '.join(missing)}")
    parameters = DispatchParameters(
        flood_limit_level=values["FLOOD_LIMIT_LEVEL_M"],
        design_flood_level=values["DESIGN_FLOOD_LEVEL_M"],
        check_flood_level=values["CHECK_FLOOD_LEVEL_M"],
        dam_top_elevation=values.get("DAM_TOP_ELEVATION_M", 253.30),
    )
    if not (
        parameters.flood_limit_level
        < parameters.design_flood_level
        < parameters.check_flood_level
    ):
        raise ValueError(
            "Level parameters must satisfy flood limit < design flood < check flood"
        )
    return parameters


def load_safe_release(path: Path) -> float:
    values = load_numeric_parameters(path)
    key = "DOWNSTREAM_SAFE_RELEASE_M3S"
    if key not in values:
        raise ValueError(f"Missing {key} in {path}")
    safe_release = values[key]
    if safe_release < 0:
        raise ValueError(f"{key} in {path} must be non-negative")
    return safe_release


def load_check_flood_peak(path: Path) -> float:
    values = load_numeric_parameters(path)
    key = "CHECK_FLOOD_PEAK_INFLOW_M3S"
    if key not in values:
        raise ValueError(f"Missing {key} in {path}")
    check_flood_peak = values[key]
    if check_flood_peak <= 0:
        raise ValueError(f"{key} in {path} must be positive")
    return check_flood_peak


def load_run_parameters(path: Path) -> RunParameters:
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if not key or not value:
                raise ValueError(f"Invalid run parameter at {path}:{line_number}")
            values[key] = value

    required = (
        "CALCULATION_START_TIME",
        "CALCULATION_END_TIME",
        "TOTAL_PERIODS",
        "TIME_STEP_HOURS",
        "INITIAL_LEVEL_M",
    )
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"Missing run parameters in {path}: {', '.join(missing)}")

    try:
        total_periods_value = float(values["TOTAL_PERIODS"])
        time_step_hours = float(values["TIME_STEP_HOURS"])
        initial_level = float(values["INITIAL_LEVEL_M"])
    except ValueError as error:
        raise ValueError(f"Numeric run parameter is invalid in {path}") from error
    if not total_periods_value.is_integer() or total_periods_value <= 0:
        raise ValueError("TOTAL_PERIODS must be a positive integer")
    total_periods = int(total_periods_value)
    if not math.isfinite(time_step_hours) or time_step_hours <= 0:
        raise ValueError("TIME_STEP_HOURS must be a finite positive number")
    if not math.isfinite(initial_level) or initial_level < 0:
        raise ValueError("INITIAL_LEVEL_M must be a finite non-negative number")

    start_time = parse_time(values["CALCULATION_START_TIME"])
    end_time = parse_time(values["CALCULATION_END_TIME"])
    try:
        actual_duration = (end_time - start_time).total_seconds()
    except TypeError as error:
        raise ValueError("Calculation start and end times must use the same timezone form") from error
    expected_duration = total_periods * time_step_hours * 3600.0
    if actual_duration <= 0:
        raise ValueError("CALCULATION_END_TIME must be later than CALCULATION_START_TIME")
    if not math.isclose(actual_duration, expected_duration, abs_tol=1e-6):
        raise ValueError(
            "Calculation duration must equal TOTAL_PERIODS * TIME_STEP_HOURS"
        )
    return RunParameters(
        calculation_start_time=start_time,
        calculation_end_time=end_time,
        total_periods=total_periods,
        time_step_hours=time_step_hours,
        initial_level=initial_level,
    )


def load_curve(path: Path) -> Curve:
    points: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            fields = re.split(r"\s+", line.strip())
            if len(fields) < 3:
                continue
            try:
                points.append((float(fields[1]), float(fields[2])))
            except ValueError:
                continue

    if len(points) < 2:
        raise ValueError(f"Curve has fewer than two numeric rows: {path}")
    if any(points[i][0] >= points[i + 1][0] for i in range(len(points) - 1)):
        raise ValueError(f"Curve x values must be strictly increasing: {path}")
    return Curve(tuple(point[0] for point in points), tuple(point[1] for point in points))


def load_hydrograph(path: Path) -> list[HydrographRow]:
    rows: list[HydrographRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        data_lines = (
            line
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        )
        reader = csv.DictReader(data_lines)
        required = {"time", "inflow_m3s"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("Input CSV must contain time and inflow_m3s columns")
        if "super_standard" in reader.fieldnames:
            raise ValueError(
                "Input CSV must not contain super_standard; it is calculated automatically"
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                inflow = float(row["inflow_m3s"])
                if not math.isfinite(inflow) or inflow < 0:
                    raise ValueError("inflow must be a finite non-negative number")
                target_outflow = parse_optional_non_negative(
                    row.get("target_outflow_m3s"), "target_outflow_m3s"
                )
                target_level = parse_optional_non_negative(
                    row.get("target_level_m"), "target_level_m"
                )
                rows.append(
                    HydrographRow(
                        time=row["time"].strip(),
                        inflow=inflow,
                        target_outflow=target_outflow,
                        target_level=target_level,
                    )
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid input at CSV line {line_number}: {error}") from error
    if not rows:
        raise ValueError("Input CSV contains no data rows")
    return rows


def parse_optional_non_negative(value: str | None, name: str) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed


def validate_control_inputs(
    rows: Sequence[HydrographRow], settings: ControlSettings
) -> None:
    if settings.mode == "OUTFLOW":
        missing = [index + 1 for index, row in enumerate(rows) if row.target_outflow is None]
        if missing:
            raise ValueError(
                f"OUTFLOW mode requires target_outflow_m3s at every period; missing period {missing[0]}"
            )
    elif settings.mode == "LEVEL":
        missing = [index + 1 for index, row in enumerate(rows) if row.target_level is None]
        if missing:
            raise ValueError(
                f"LEVEL mode requires target_level_m at every period; missing period {missing[0]}"
            )


def interval_seconds(rows: Sequence[HydrographRow], step_hours: float | None) -> list[float]:
    if step_hours is not None:
        if not math.isfinite(step_hours) or step_hours <= 0:
            raise ValueError("--step-hours must be a finite positive number")
        return [step_hours * 3600.0] * len(rows)

    if len(rows) < 2:
        raise ValueError("A one-row input requires --step-hours")
    times = [parse_time(row.time) for row in rows]
    steps = [(times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)]
    if any(step <= 0 for step in steps):
        raise ValueError("Input times must be strictly increasing")
    steps.append(steps[-1])
    return steps


def validate_run_window(
    rows: Sequence[HydrographRow],
    parameters: RunParameters,
    time_step_hours: float,
) -> None:
    if len(rows) != parameters.total_periods:
        raise ValueError(
            f"Input row count {len(rows)} does not match TOTAL_PERIODS "
            f"{parameters.total_periods}"
        )
    for index, row in enumerate(rows):
        actual_time = parse_time(row.time)
        expected_time = parameters.calculation_start_time + timedelta(
            hours=index * time_step_hours
        )
        try:
            difference = abs((actual_time - expected_time).total_seconds())
        except TypeError as error:
            raise ValueError(
                "Input times and run-parameter times must use the same timezone form"
            ) from error
        if difference > 1e-6:
            raise ValueError(
                f"Input time at period {index + 1} is {row.time}; "
                f"expected {format_time(expected_time)}"
            )
    calculated_end = parameters.calculation_start_time + timedelta(
        hours=parameters.total_periods * time_step_hours
    )
    try:
        end_difference = abs(
            (calculated_end - parameters.calculation_end_time).total_seconds()
        )
    except TypeError as error:
        raise ValueError(
            "Calculation start and end times must use the same timezone form"
        ) from error
    if end_difference > 1e-6:
        raise ValueError(
            "Effective time step is inconsistent with calculation start/end times"
        )


def available_release(
    level: float,
    outflow_curve: Curve,
    separate_outlet_capacity: float,
    max_release: float | None = None,
) -> tuple[float, bool]:
    if max_release is not None:
        return max_release, False
    if level <= outflow_curve.x[0]:
        return separate_outlet_capacity, False
    curve_release, extrapolated = outflow_curve.value(level)
    return max(0.0, curve_release) + separate_outlet_capacity, extrapolated


def project_max_level(
    initial_storage: float,
    inflows: Sequence[float],
    steps: Sequence[float],
    storage_to_level: Curve,
    outflow_curve: Curve,
    release_mode: str,
    normal_release: float,
    safe_release: float,
    separate_outlet_capacity: float,
    max_release: float | None = None,
) -> tuple[float, bool]:
    storage = initial_storage
    level, extrapolated = storage_to_level.value(storage)
    max_level = level
    for inflow, step in zip(inflows, steps):
        level, level_extrapolated = storage_to_level.value(storage)
        capacity, outflow_extrapolated = available_release(
            level, outflow_curve, separate_outlet_capacity, max_release
        )
        if release_mode == "full":
            release = capacity
        elif release_mode == "safe":
            release = min(safe_release, capacity)
        else:
            release = min(normal_release, capacity)
        water_limited_release = inflow + max(0.0, storage - storage_to_level.x[0]) * VOLUME_FACTOR / step
        release = min(release, water_limited_release)
        storage += (inflow - release) * step / VOLUME_FACTOR
        projected_level, storage_extrapolated = storage_to_level.value(storage)
        max_level = max(max_level, projected_level)
        extrapolated = (
            extrapolated
            or level_extrapolated
            or outflow_extrapolated
            or storage_extrapolated
        )
    return max_level, extrapolated


def project_next_safe_level(
    initial_storage: float,
    inflow: float,
    step: float,
    storage_to_level: Curve,
    outflow_curve: Curve,
    safe_release: float,
    separate_outlet_capacity: float,
    max_release: float | None = None,
) -> tuple[float, bool]:
    level, level_extrapolated = storage_to_level.value(initial_storage)
    capacity, outflow_extrapolated = available_release(
        level, outflow_curve, separate_outlet_capacity, max_release
    )
    release = min(safe_release, capacity)
    water_limited_release = (
        inflow
        + max(0.0, initial_storage - storage_to_level.x[0]) * VOLUME_FACTOR / step
    )
    release = min(release, water_limited_release)
    end_storage = initial_storage + (inflow - release) * step / VOLUME_FACTOR
    end_level, storage_extrapolated = storage_to_level.value(end_storage)
    return end_level, (
        level_extrapolated or outflow_extrapolated or storage_extrapolated
    )


def required_constant_release(
    initial_storage: float,
    limit_storage: float,
    inflows: Sequence[float],
    steps: Sequence[float],
) -> float:
    cumulative_volume = (initial_storage - limit_storage) * VOLUME_FACTOR
    cumulative_seconds = 0.0
    required = 0.0
    for inflow, step in zip(inflows, steps):
        cumulative_volume += inflow * step
        cumulative_seconds += step
        required = max(required, cumulative_volume / cumulative_seconds)
    return max(0.0, required)


def forecast_metrics(
    storage: float,
    inflows: Sequence[float],
    steps: Sequence[float],
    storage_to_level: Curve,
    outflow_curve: Curve,
    limit_storage: float,
    normal_release: float,
    safe_release: float,
    separate_outlet_capacity: float,
    max_release: float | None = None,
) -> ForecastMetrics:
    max_base, base_extrapolated = project_max_level(
        storage,
        inflows,
        steps,
        storage_to_level,
        outflow_curve,
        "base",
        normal_release,
        safe_release,
        separate_outlet_capacity,
        max_release,
    )
    max_safe, safe_extrapolated = project_max_level(
        storage,
        inflows,
        steps,
        storage_to_level,
        outflow_curve,
        "safe",
        normal_release,
        safe_release,
        separate_outlet_capacity,
        max_release,
    )
    max_full, full_extrapolated = project_max_level(
        storage,
        inflows,
        steps,
        storage_to_level,
        outflow_curve,
        "full",
        normal_release,
        safe_release,
        separate_outlet_capacity,
        max_release,
    )
    next_safe, next_safe_extrapolated = project_next_safe_level(
        storage,
        inflows[0],
        steps[0],
        storage_to_level,
        outflow_curve,
        safe_release,
        separate_outlet_capacity,
        max_release,
    )
    return ForecastMetrics(
        peak_inflow=max(inflows),
        max_level_base=max_base,
        next_level_safe=next_safe,
        max_level_safe=max_safe,
        max_level_full=max_full,
        required_release=required_constant_release(storage, limit_storage, inflows, steps),
        extrapolated=(
            base_extrapolated
            or safe_extrapolated
            or full_extrapolated
            or next_safe_extrapolated
        ),
    )


def select_release(
    level: float,
    metrics: ForecastMetrics,
    super_standard: bool,
    safe_release: float,
    normal_release: float,
    capacity: float,
    parameters: DispatchParameters,
    rules: RuleSettings,
) -> tuple[int, float]:
    if super_standard or metrics.max_level_full > parameters.check_flood_level + LEVEL_TOLERANCE:
        return 4, release_for_action(
            rules.emergency_release, metrics, safe_release, normal_release, capacity
        )

    rising_above_limit = (
        level >= parameters.flood_limit_level - LEVEL_TOLERANCE
        and metrics.next_level_safe > level + LEVEL_TOLERANCE
    )
    imminent_limit_crossing = (
        level < parameters.flood_limit_level - LEVEL_TOLERANCE
        and metrics.next_level_safe > parameters.flood_limit_level + LEVEL_TOLERANCE
    )
    if rising_above_limit or imminent_limit_crossing:
        return 2, release_for_action(
            rules.full_capacity_release, metrics, safe_release, normal_release, capacity
        )
    if level > parameters.flood_limit_level + LEVEL_TOLERANCE:
        return 3, release_for_action(
            rules.drawdown_release, metrics, safe_release, normal_release, capacity
        )
    if metrics.max_level_base > parameters.flood_limit_level + LEVEL_TOLERANCE:
        return 1, release_for_action(
            rules.prerelease_release, metrics, safe_release, normal_release, capacity
        )
    return 0, release_for_action(
        rules.normal_release, metrics, safe_release, normal_release, capacity
    )


def release_for_action(
    action: str,
    metrics: ForecastMetrics,
    safe_release: float,
    normal_release: float,
    capacity: float,
) -> float:
    if action == "AVAILABLE_CAPACITY":
        return capacity
    if action == "MIN_CAPACITY_SAFE":
        return min(capacity, safe_release)
    if action == "MIN_REQUIRED_PEAK_SAFE_CAPACITY":
        return min(
            metrics.required_release,
            metrics.peak_inflow,
            safe_release,
            capacity,
        )
    if action == "MIN_NORMAL_SAFE_CAPACITY":
        return min(normal_release, safe_release, capacity)
    raise ValueError(f"Unsupported release action: {action}")


def limit_manual_release(
    target: float,
    capacity: float,
    safe_release: float,
    apply_safe_release_limit: bool,
) -> tuple[float, str]:
    release = min(target, capacity)
    status = "TARGET_MET" if release >= target - LEVEL_TOLERANCE else "LIMITED_BY_CAPACITY"
    if apply_safe_release_limit and safe_release < release:
        release = safe_release
        status = "LIMITED_BY_SAFE_RELEASE"
    return max(0.0, release), status


def validate_non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")


def is_super_standard_flood(input_peak: float, check_flood_peak: float) -> bool:
    return input_peak > check_flood_peak + LEVEL_TOLERANCE


def calculate_dispatch(
    rows: Sequence[HydrographRow],
    steps: Sequence[float],
    *,
    initial_level: float,
    storage_curve: Curve,
    outflow_curve: Curve,
    parameters: DispatchParameters,
    rules: RuleSettings,
    mode: str = "RULE",
    safe_release: float = 30.0,
    check_flood_peak: float = 679.0,
    normal_release: float = 0.0,
    outlet_capacity: float = 0.0,
    forecast_steps: int | None = None,
    max_release: float | None = None,
    initial_storage: float | None = None,
    input_peak_inflow: float | None = None,
) -> list[dict]:
    """Run the same numerical model in memory for CLI and application callers.

    Results retain full precision; CSV rounding happens only when exporting.
    """
    if mode not in {"RULE", "OUTFLOW", "LEVEL"}:
        raise ValueError("Control mode must be RULE, OUTFLOW, or LEVEL")
    if len(steps) != len(rows) or any(not math.isfinite(step) or step <= 0 for step in steps):
        raise ValueError("Each input period requires a finite positive time step")
    if forecast_steps is not None and (forecast_steps <= 0 or int(forecast_steps) != forecast_steps):
        raise ValueError("forecast_steps must be a positive integer")
    for name, value in (("initial_level", initial_level), ("safe_release", safe_release),
                        ("check_flood_peak", check_flood_peak), ("normal_release", normal_release),
                        ("outlet_capacity", outlet_capacity)):
        validate_non_negative(name, value)
    if check_flood_peak <= 0:
        raise ValueError("check_flood_peak must be positive")
    if max_release is not None:
        validate_non_negative("max_release", max_release)
    if initial_storage is not None:
        validate_non_negative("initial_storage", initial_storage)
    if input_peak_inflow is not None:
        validate_non_negative("input_peak_inflow", input_peak_inflow)
    effective_safe_release = safe_release if max_release is None else max_release
    for row in rows:
        validate_non_negative("inflow_m3s", row.inflow)
        if row.target_outflow is not None:
            validate_non_negative("target_outflow_m3s", row.target_outflow)
        if row.target_level is not None:
            validate_non_negative("target_level_m", row.target_level)
    validate_control_inputs(rows, ControlSettings(mode, Path("Dispatch_Rules.DAT")))
    if not rows:
        return []
    storage_to_level = storage_curve.inverse()
    storage = initial_storage
    if storage is None:
        storage, _ = storage_curve.value(initial_level)
    limit_storage, _ = storage_curve.value(parameters.flood_limit_level)
    # A continuation keeps the original process-wide flood classification.
    peak = max(row.inflow for row in rows)
    automatic_super_standard = is_super_standard_flood(
        max(peak, input_peak_inflow) if input_peak_inflow is not None else peak,
        check_flood_peak,
    )
    output = []
    for index, row in enumerate(rows):
        forecast_end = len(rows) if forecast_steps is None else min(len(rows), index + forecast_steps)
        inflows = [item.inflow for item in rows[index:forecast_end]]
        level, level_extrapolated = storage_to_level.value(storage)
        design_capacity, _ = available_release(level, outflow_curve, outlet_capacity)
        capacity, outflow_extrapolated = available_release(level, outflow_curve, outlet_capacity, max_release)
        metrics = forecast_metrics(storage, inflows, steps[index:forecast_end], storage_to_level,
                                   outflow_curve, limit_storage, normal_release, effective_safe_release, outlet_capacity, max_release)
        emergency_required = automatic_super_standard or metrics.max_level_full > parameters.check_flood_level + LEVEL_TOLERANCE
        target_extrapolated = False
        if mode == "RULE":
            state_code, release = select_release(level, metrics, automatic_super_standard, effective_safe_release,
                                                 normal_release, capacity, parameters, rules)
            control_status = f"RULE_{STATE_NAMES[state_code]}"
        elif rules.emergency_override_manual and emergency_required:
            state_code, release = 4, release_for_action(rules.emergency_release, metrics, effective_safe_release, normal_release, capacity)
            control_status = "EMERGENCY_OVERRIDE"
        elif mode == "OUTFLOW":
            state_code = 5
            release, control_status = limit_manual_release(row.target_outflow or 0.0, capacity, effective_safe_release,
                                                           rules.manual_apply_safe_release_limit)
        else:
            state_code = 6
            assert row.target_level is not None
            target_storage, target_extrapolated = storage_curve.value(row.target_level)
            required_for_level = row.inflow + (storage - target_storage) * VOLUME_FACTOR / steps[index]
            release, control_status = limit_manual_release(max(0.0, required_for_level), capacity, effective_safe_release,
                                                           rules.manual_apply_safe_release_limit)
            if required_for_level < 0:
                control_status = "TARGET_REQUIRES_STORAGE_INCREASE"
        if max_release is not None and control_status == "LIMITED_BY_CAPACITY":
            control_status = "LIMITED_BY_USER_MAX_RELEASE"
        water_limited = row.inflow + max(0.0, storage - storage_to_level.x[0]) * VOLUME_FACTOR / steps[index]
        if water_limited < release:
            release = water_limited
            if mode != "RULE":
                control_status = "LIMITED_BY_WATER_AVAILABLE"
        end_storage = storage + (row.inflow - release) * steps[index] / VOLUME_FACTOR
        end_level, end_extrapolated = storage_to_level.value(end_storage)
        output.append({
            "time": row.time, "inflow_m3s": row.inflow, "state": STATE_NAMES[state_code],
            "control_status": control_status, "start_level_m": level, "start_storage_1e4m3": storage,
            "forecast_peak_m3s": metrics.peak_inflow, "forecast_max_level_base_m": metrics.max_level_base,
            "forecast_next_level_safe_m": metrics.next_level_safe, "forecast_max_level_safe_m": metrics.max_level_safe,
            "forecast_max_level_full_m": metrics.max_level_full,
            "design_level_exceeded": int(max(level, metrics.max_level_full) > parameters.design_flood_level + LEVEL_TOLERANCE),
            "required_release_m3s": metrics.required_release, "available_release_m3s": capacity,
            "design_available_release_m3s": design_capacity, "max_release_m3s": max_release,
            "release_m3s": release, "end_storage_1e4m3": end_storage, "end_level_m": end_level,
            "curve_extrapolated": int(level_extrapolated or outflow_extrapolated or metrics.extrapolated or target_extrapolated or end_extrapolated),
        })
        storage = end_storage
    return output
