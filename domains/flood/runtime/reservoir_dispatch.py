"""Adapt the native reservoir engine to Longtan rainfall-runoff inputs."""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from . import reservoir_engine as engine


PROJECT_DIR = Path(__file__).resolve().parents[3]
DISPATCH_DATA_DIR = PROJECT_DIR / "domains" / "flood" / "data" / "dispatch"
MODEL_SOURCE = "domains/flood/runtime/reservoir_engine.py"
CONTROL_MODES = {"RULE": "规程调度", "OUTFLOW": "目标下泄流量", "LEVEL": "目标库水位"}

@dataclass(frozen=True)
class DispatchSettings:
    """Scenario settings; max_release replaces both curve capacity and safe release."""

    mode: str = "RULE"
    initial_level_m: float = 245.10
    target_outflow_m3s: float | None = None
    target_level_m: float | None = None
    normal_release_m3s: float = 0.0
    outlet_capacity_m3s: float = 0.0
    forecast_steps: int = 25
    max_release_m3s: float | None = None

    def validate(self) -> None:
        if self.mode not in CONTROL_MODES:
            raise ValueError("调度方式必须为 RULE、OUTFLOW 或 LEVEL")
        for key in ("initial_level_m", "normal_release_m3s", "outlet_capacity_m3s", "target_outflow_m3s", "target_level_m", "max_release_m3s"):
            value = getattr(self, key)
            if value is None and (key.startswith("target_") or key == "max_release_m3s"):
                continue
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} 必须为有限非负数")
        if isinstance(self.forecast_steps, bool) or not isinstance(self.forecast_steps, int) or self.forecast_steps <= 0:
            raise ValueError("forecast_steps 必须为正整数")

    def public(self) -> dict[str, Any]:
        return {**asdict(self), "mode_label": CONTROL_MODES[self.mode], "reservoir_id": "longtan",
                "model_source": MODEL_SOURCE}


def default_dispatch_settings() -> DispatchSettings:
    # The file's sample dates apply only to the standalone CSV command. Runtime
    # periods and time steps come from the selected rainfall process.
    run = engine.load_run_parameters(DISPATCH_DATA_DIR / "dispatch_run_parameters.dat")
    return DispatchSettings(initial_level_m=run.initial_level)


def parse_dispatch_settings(value: dict[str, Any]) -> DispatchSettings:
    if not isinstance(value, dict):
        raise ValueError("调度参数必须为对象")
    unknown = set(value) - set(DispatchSettings.__dataclass_fields__)
    if unknown:
        raise ValueError(f"未知调度参数：{', '.join(sorted(unknown))}")
    settings = replace(default_dispatch_settings(), **value)
    settings.validate()
    return settings


def dispatch_model_signature() -> str:
    """Bind trial continuations to the curves and rules used by the baseline."""
    digest = hashlib.sha256()
    for path in [Path(engine.__file__), *sorted(DISPATCH_DATA_DIR.glob("*.dat"))]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class DispatchParameters:
    """Optional parameter overrides retained for programmatic callers."""
    flood_limit_level_m: float = 245.30
    design_flood_level_m: float = 247.92
    check_flood_level_m: float = 248.91
    downstream_safe_release_m3s: float = 30.0
    check_flood_peak_inflow_m3s: float = 679.0
    initial_level_m: float = 245.10
    normal_release_m3s: float = 0.0
    forecast_steps: int = 25
    dam_top_elevation_m: float = 253.30


def simulate_reservoir_dispatch(
    inflow_series: Iterable[dict[str, Any] | float | int], *,
    settings: DispatchSettings | None = None, parameters: DispatchParameters | None = None,
    initial_level_m: float | None = None, dt_hours: float = 1.0,
    initial_storage_1e4m3: float | None = None,
    input_peak_inflow_m3s: float | None = None,
) -> dict[str, Any]:
    """Run all periods through the shared engine and add application telemetry.

    Per-period manual targets take precedence over scenario-wide targets.
    Keep the supplied engine's water floor, emergency priority and user capacity
    override semantics; UI and agent consumers receive the actual release.
    """
    if not math.isfinite(dt_hours) or dt_hours <= 0:
        raise ValueError("dt_hours must be finite and positive")
    config = settings or default_dispatch_settings()
    levels = engine.load_dispatch_parameters(DISPATCH_DATA_DIR / "reservoir_level_parameters.dat")
    safe_release = engine.load_safe_release(DISPATCH_DATA_DIR / "downstream_safe_release.dat")
    check_peak = engine.load_check_flood_peak(DISPATCH_DATA_DIR / "check_flood_peak_inflow.dat")
    if parameters is not None:
        levels = engine.DispatchParameters(parameters.flood_limit_level_m, parameters.design_flood_level_m,
                                          parameters.check_flood_level_m, parameters.dam_top_elevation_m)
        safe_release, check_peak = parameters.downstream_safe_release_m3s, parameters.check_flood_peak_inflow_m3s
        config = replace(config, initial_level_m=parameters.initial_level_m, normal_release_m3s=parameters.normal_release_m3s,
                         forecast_steps=parameters.forecast_steps)
    if initial_level_m is not None:
        config = replace(config, initial_level_m=float(initial_level_m))
    config.validate()
    control = engine.load_control_settings(DISPATCH_DATA_DIR / "dispatch_control_mode.dat")
    rules = engine.load_rule_settings(control.rule_file)
    outflow_curve = engine.load_curve(DISPATCH_DATA_DIR / "outflow_curve.dat")
    design_release, _ = engine.available_release(levels.design_flood_level, outflow_curve, 0)
    design_release = round(design_release, 2)
    warnings = []
    if config.max_release_m3s is not None and config.max_release_m3s > design_release:
        warnings.append({"code": "max_release_exceeds_design", "max_release_m3s": config.max_release_m3s,
                         "design_release_m3s": round(design_release, 6),
                         "message": f"设定的最大下泄流量 {config.max_release_m3s:.2f} m³/s 大于设计下泄参考值 {design_release:.2f} m³/s；本次仍按用户设定的上限计算。",
                         "design_release_basis": "设计洪水位247.92 m对应原泄流曲线"})
    rows = []
    for index, item in enumerate(inflow_series):
        point = item if isinstance(item, dict) else {"inflow_m3s": item}
        inflow = float(point.get("reservoir_inflow_m3s", point.get("inflow_m3s")))
        targets = {}
        for key in ("target_outflow_m3s", "target_level_m"):
            value = point.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                value = getattr(config, key)
            targets[key] = None if value is None else float(value)
        rows.append(engine.HydrographRow(str(point.get("valid_time") or point.get("time") or index), inflow,
                                        targets["target_outflow_m3s"], targets["target_level_m"]))
    calculated = engine.calculate_dispatch(
        rows, [dt_hours * 3600.0] * len(rows), initial_level=config.initial_level_m,
        storage_curve=engine.load_curve(DISPATCH_DATA_DIR / "storage_capacity_curve.dat"),
        outflow_curve=outflow_curve, parameters=levels, rules=rules,
        mode=config.mode, safe_release=safe_release, check_flood_peak=check_peak,
        normal_release=config.normal_release_m3s, outlet_capacity=config.outlet_capacity_m3s, forecast_steps=config.forecast_steps,
        max_release=config.max_release_m3s,
        initial_storage=initial_storage_1e4m3,
        input_peak_inflow=input_peak_inflow_m3s,
    )
    peak = max((row.inflow for row in rows), default=0.0)
    if input_peak_inflow_m3s is not None:
        peak = max(peak, input_peak_inflow_m3s)
    super_standard = engine.is_super_standard_flood(peak, check_peak)
    thresholds = {"flood_limit_level_m": levels.flood_limit_level, "design_flood_level_m": levels.design_flood_level,
                  "check_flood_level_m": levels.check_flood_level, "downstream_safe_release_m3s": safe_release,
                  "check_flood_peak_inflow_m3s": check_peak, "dam_top_elevation_m": levels.dam_top_elevation,
                  "design_release_m3s": round(design_release, 6), "max_release_m3s": config.max_release_m3s}
    output = []
    for index, (row, source) in enumerate(zip(calculated, rows)):
        reason_code, reason = _dispatch_reason(row, config.mode, levels.flood_limit_level, super_standard)
        status = row["control_status"]
        constraint = "当前水位下泄流能力为零，计算泄流为零。" if row["available_release_m3s"] <= 0 else CONTROL_STATUS_LABELS.get(status, "计算泄流受调度目标、泄流能力及可用水量约束。")
        if config.max_release_m3s is not None:
            reason += f" 本次按用户设定的最大下泄流量 {config.max_release_m3s:.2f} m³/s 作为上限。"
            constraint = "实际下泄不超过用户设定上限，并受可用水量约束。"
        output.append({
            **{key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()},
            "index": index, "valid_time": source.time, "control_mode": config.mode,
            "control_mode_label": CONTROL_MODES[config.mode], "control_status_label": CONTROL_STATUS_LABELS.get(status, "按规程计算"),
            "target_outflow_m3s": source.target_outflow, "target_level_m": source.target_level,
            "reason_code": reason_code, "reason": reason, "constraint": constraint, "mode": "simulation",
            "model_source": MODEL_SOURCE, "thresholds": thresholds,
            "warnings": warnings,
            "continuation_state": {
                "storage_1e4m3": row["end_storage_1e4m3"],
                "level_m": row["end_level_m"],
                "input_peak_inflow_m3s": peak,
            },
            "curve_extrapolated": bool(row["curve_extrapolated"]), "forecast_peak_inflow_m3s": round(row["forecast_peak_m3s"], 6),
        })
    return {"status": "completed", "settings": config.public(), "warnings": warnings,
            "parameters": {**asdict(config), **thresholds, "check_flood_peak_inflow_m3s": check_peak},
            "series": output, "peak_inflow_m3s": round(peak, 6),
            "max_level_m": max((row["end_level_m"] for row in output), default=config.initial_level_m),
            "super_standard_flood": super_standard}


CONTROL_STATUS_LABELS = {
    "LIMITED_BY_USER_MAX_RELEASE": "受用户设定的最大下泄流量限制",
    "RULE_NORMAL": "按规程正常蓄水", "RULE_PRERELEASE": "按规程预泄",
    "RULE_DRAWDOWN": "按规程退水腾库", "RULE_FULL_CAPACITY": "按规程全能力下泄",
    "RULE_EMERGENCY": "按规程应急全能力下泄",
    "TARGET_MET": "目标已达到", "LIMITED_BY_CAPACITY": "受当前泄流能力限制",
    "LIMITED_BY_SAFE_RELEASE": "受下游安全泄量限制", "LIMITED_BY_WATER_AVAILABLE": "受可用水量限制",
    "TARGET_REQUIRES_STORAGE_INCREASE": "目标水位所需蓄水量超过本时段可获得水量，下泄取零",
    "EMERGENCY_OVERRIDE": "应急全能力下泄覆盖手动目标",
}


def _dispatch_reason(row: dict, mode: str, flood_limit: float, super_standard: bool) -> tuple[str, str]:
    state = row["state"]
    if state == "EMERGENCY":
        return ("inflow_peak_exceeded", "输入过程洪峰超过校核入库洪峰阈值，进入应急全能力下泄判定。") if super_standard else (
            "full_release_above_check", "预见期内即使按全能力下泄，最高水位仍预计超过校核水位。")
    if mode != "RULE":
        return row["control_status"].lower(), CONTROL_STATUS_LABELS[row["control_status"]]
    reasons = {
        "NORMAL": ("normal_storage", "预见期未触发防洪控制条件，按正常下泄任务计算。"),
        "PRERELEASE": ("forecast_above_flood_limit", "按正常下泄预测水位将超过汛限水位，进入预泄判定。"),
        "DRAWDOWN": ("above_limit_receding", "当前高于汛限水位，按安全泄量下泄时不再上涨，进入退水腾库。"),
        "FULL_CAPACITY": ("above_limit_rising", "当前不低于汛限水位，按安全泄量仍会上涨，按当前能力下泄。"),
    }
    if state == "FULL_CAPACITY" and row["start_level_m"] < flood_limit:
        return "imminent_limit_crossing", "按安全泄量下泄，本时段末仍将超过汛限水位，按当前能力下泄。"
    return reasons[state]


__all__ = [
    "DispatchParameters", "DispatchSettings", "default_dispatch_settings",
    "parse_dispatch_settings", "simulate_reservoir_dispatch",
]
