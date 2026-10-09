"""Compare future rainfall assumptions without replacing the live forecast."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import csv
import math
import uuid

import numpy as np

from .boundary_flow import (
    BOUNDARIES, INTERVAL_FLOW_SCALE, calculate_basin_runoff,
    load_boundary_flow_rows, rainfall_context_from_rows, rainfall_model_signature,
)
from .cnn_v2 import run_cnn_v2_forecast
from .common import rel
from .dispatch_trial import (
    _comparison, _impact, _load_basis, _load_depth_series, _save, _settings,
    reservoir_safety,
)
from .hydrodynamic_grid import offset_time_iso
from .impact_analysis import resolve_target_types
from .impact_scope import ImpactScope
from .rainfall_input import BASIN_RAINFALL_COLUMNS, display_rainfall_mm
from .reservoir_dispatch import simulate_reservoir_dispatch
from .workspace import active_workspace_id, workspace_dir


CONTROL_COLUMNS = ("target_outflow_m3s", "target_level_m")


def _number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为有限非负数") from exc
    if isinstance(value, bool) or not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} 必须为有限非负数")
    return result


def _rainfall_basis(snapshot: dict, context: dict) -> tuple[list[dict], int, str]:
    forcing = snapshot.get("rainfall_runoff_context")
    source = "forecast_input_snapshot"
    if not forcing:
        # Older predictions have no forcing snapshot. Only accept the original
        # workspace CSV after reproducing its t0 state and all boundary inputs.
        path = workspace_dir() / "inputs" / "rainfall.csv"
        if not path.is_file():
            raise ValueError("原预测缺少降水输入快照，请重新生成预测后模拟")
        rows = load_boundary_flow_rows(path, dispatch_settings=_settings(context))
        anchor = next((r for r in rows if r["observed_at"] == context["t0"]), None)
        if anchor is None or any(not math.isclose(
            anchor["reservoir_dispatch"][key], context["baseline_series"][0][key], abs_tol=1e-5,
        ) for key in ("end_level_m", "end_storage_1e4m3", "release_m3s")):
            raise ValueError("降水输入不能复现原预测 t0 水库状态，请重新生成预测")
        for key in BOUNDARIES:
            actual = [r["boundaries"][key]["flow_m3s"] for r in rows[anchor["sequence"]:anchor["sequence"] + 25]]
            expected = [p["flow_m3s"] for p in snapshot["summary"]["boundaries"][key]["series"]]
            if len(actual) != 25 or not np.allclose(actual, expected, rtol=0, atol=1e-5):
                raise ValueError("降水输入不能复现原预测四边界，请重新生成预测")
        forcing = rainfall_context_from_rows(rows)
        source = "verified_workspace_csv"
    if forcing.get("model_signature") != rainfall_model_signature():
        raise ValueError("降雨产流模型或参数已变化，请重新生成预测后模拟")
    inputs = deepcopy(forcing.get("series") or [])
    if not inputs:
        raise ValueError("原预测降水输入为空")
    t0 = datetime.fromisoformat(context["t0"])
    offsets = []
    for row in inputs:
        offsets.append((datetime.fromisoformat(row["valid_time"]) - t0).total_seconds() / 3600)
        for column in BASIN_RAINFALL_COLUMNS.values():
            row[column] = _number(row[column], column)
    if 0 not in offsets or any(b - a != 1 for a, b in zip(offsets, offsets[1:])):
        raise ValueError("降水输入缺少 t0 或不是连续小时数据")
    anchor = offsets.index(0)
    if offsets[anchor:anchor + 25] != list(range(25)):
        raise ValueError("降水输入未覆盖 t0 至未来24小时")
    # Verify the full tail, since RULE uses look-ahead beyond the output window.
    runoff = calculate_basin_runoff(inputs)
    future = runoff["reservoir"][anchor + 1:]
    expected = context["future_inflows"]
    if len(future) != len(expected) or any(
        row["valid_time"] != old["valid_time"]
        or not math.isclose(row["reservoir_inflow_m3s"], old["inflow_m3s"], abs_tol=1e-5)
        or any(inputs[anchor + i + 1].get(key) != old.get(key) for key in CONTROL_COLUMNS)
        for i, (row, old) in enumerate(zip(future, expected))
    ):
        raise ValueError("降水过程与原预测入库流量或调度目标不匹配，请重新生成预测")
    for key, basin, scale in (("interval1", "interval1", INTERVAL_FLOW_SCALE),
                              ("interval2", "interval2", INTERVAL_FLOW_SCALE),
                              ("tonggu", "interval2", INTERVAL_FLOW_SCALE * 0.946)):
        actual = [r["reservoir_inflow_m3s"] * scale for r in runoff[basin][anchor:anchor + 25]]
        expected_flows = [p["flow_m3s"] for p in snapshot["summary"]["boundaries"][key]["series"]]
        if not np.allclose(actual, expected_flows, rtol=0, atol=1e-5):
            raise ValueError("降水过程与原预测区间边界不匹配，请重新生成预测")
    return inputs, anchor, source


def _candidate(snapshot: dict, context: dict, inputs: list[dict], anchor: int,
               scenario_id: str) -> tuple[dict, dict]:
    runoff = calculate_basin_runoff(inputs)
    future = [
        {**point, **{key: raw.get(key) for key in CONTROL_COLUMNS}}
        for point, raw in zip(runoff["reservoir"][anchor + 1:], inputs[anchor + 1:])
    ]
    state = context["state"]
    dispatch = simulate_reservoir_dispatch(
        future, settings=_settings(context), dt_hours=1,
        initial_level_m=state["level_m"], initial_storage_1e4m3=state["storage_1e4m3"],
        # The stored peak includes the old future. Recompute it from unchanged
        # history plus assumed future, so reducing rainfall can reduce the peak.
        input_peak_inflow_m3s=max(row["reservoir_inflow_m3s"] for row in runoff["reservoir"]),
    )
    candidate = deepcopy(snapshot)
    candidate.pop("reservoir_dispatch_context", None)
    candidate["rainfall_runoff_context"] = {"model_signature": rainfall_model_signature(), "series": inputs}
    candidate["boundary_flow_id"] = scenario_id
    candidate.pop("forecast_trigger", None)
    summary = candidate["summary"]
    summary.pop("input_path", None)
    summary.update(boundary_flow_id=scenario_id, mode="rainfall_assumption")
    for key in BOUNDARIES:
        boundary = summary["boundaries"][key]
        for hour, point in enumerate(boundary["series"][1:], 1):
            if key == "upstream":
                value = dispatch["series"][hour - 1]["release_m3s"]
            else:
                basin = "interval2" if key == "tonggu" else key
                scale = INTERVAL_FLOW_SCALE * (0.946 if key == "tonggu" else 1)
                value = runoff[basin][anchor + hour]["reservoir_inflow_m3s"] * scale
            point.update(flow_m3s=round(value, 6), source="rainfall_assumption_runoff_dispatch")
        flows = [point["flow_m3s"] for point in boundary["series"]]
        boundary.update(peak_flow_m3s=round(max(flows), 3), mean_flow_m3s=round(sum(flows) / len(flows), 3),
                        first_flow_m3s=round(flows[0], 3), last_flow_m3s=round(flows[-1], 3))
    summary["rainfall_series"] = [
        {"time_h": i, "valid_time": row["valid_time"], "rainfall_mm": round(display_rainfall_mm(row), 3),
         **{column: row[column] for column in BASIN_RAINFALL_COLUMNS.values()}}
        for i, row in enumerate(inputs[anchor:anchor + 25])
    ]
    total = round(sum(row["rainfall_mm"] for row in summary["rainfall_series"]), 3)
    summary.update(rainfall_total_mm=total, predicted_rainfall_24h_mm=total)
    return candidate, dispatch


def _inundation(values: np.ndarray) -> dict:
    return {"wet_cell_count": int(np.count_nonzero(values > 0)),
            "max_depth_m": round(float(values.max()), 4)}


def simulate_flood_scenario(
    resolver, rainfall_multiplier: float, forecast_id: str = "latest",
    from_time_h: float = 0.0, to_time_h: float = 24.0,
    time_h: float | None = None, target_type: str = "Road",
    object_ids: list[str] | None = None,
) -> dict:
    multiplier = _number(rainfall_multiplier, "rainfall_multiplier")
    start_h = _number(from_time_h, "from_time_h")
    end_h = _number(to_time_h, "to_time_h")
    if not 0 <= start_h < end_h <= 24 or not start_h.is_integer() or not end_h.is_integer():
        raise ValueError("降水调整范围须为 0 至24之间的整点小时，且起点小于终点；调整 (from_time_h, to_time_h] 时段")
    metadata, snapshot, context = _load_basis(forecast_id)
    target_types = resolve_target_types(target_type)
    if not target_types:
        raise ValueError("不支持的影响对象类型")
    ImpactScope(resolver, target_types, object_ids, None)
    baseline_array, steps = _load_depth_series(workspace_dir() / "forecasts" / metadata["forecast_id"])
    hour = _number(0 if time_h is None else time_h, "time_h")
    matches = [i for i, value in enumerate(steps) if math.isclose(value, hour, abs_tol=1e-6)]
    if not matches:
        raise ValueError("原预测没有目标时刻的精确切片，不能改用其他时刻或最大包络")
    index = matches[0]
    time_h = steps[index]
    inputs, anchor, source = _rainfall_basis(snapshot, context)
    original_inputs = deepcopy(inputs)
    for i in range(anchor + int(start_h) + 1, anchor + int(end_h) + 1):
        for column in BASIN_RAINFALL_COLUMNS.values():
            inputs[i][column] = _number(inputs[i][column] * multiplier, column)
    scenario_id = f"rainfall_scenario_{uuid.uuid4().hex}"
    candidate_input, dispatch = _candidate(snapshot, context, inputs, anchor, scenario_id)
    series = dispatch["series"][:24]
    folder = workspace_dir() / "rainfall_scenarios" / scenario_id
    folder.mkdir(parents=True)
    assumption = {"rainfall_multiplier": multiplier, "from_time_h": start_h, "to_time_h": end_h,
                  "from_at": offset_time_iso(context["t0"], start_h),
                  "to_at": offset_time_iso(context["t0"], end_h),
                  "interval": "(from_time_h, to_time_h]；小时累计降水按时段终点标记，t0 及以前不改写",
                  "basins": list(BASIN_RAINFALL_COLUMNS)}
    candidate_input["scenario"] = {"assumption": assumption, "baseline_forecast_id": metadata["forecast_id"],
                                   "applied": False, "initial_reservoir_state": context["state"]}
    _save(folder / "input.json", candidate_input)
    _save(folder / "baseline_input.json", snapshot)
    # Save full forcing history and look-ahead, not just the 24-hour window.
    fields = ["time_period_end", *BASIN_RAINFALL_COLUMNS.values(), *CONTROL_COLUMNS]
    for filename, rows in (("baseline_rainfall.csv", original_inputs), ("rainfall.csv", inputs)):
        with (folder / filename).open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({"time_period_end": datetime.fromisoformat(row["valid_time"]).strftime("%Y-%m-%d %H:%M"),
                                 **{key: row.get(key) for key in fields[1:]}})
    settings = _settings(context).public()
    _save(folder / "reservoir.json", {"settings": settings, "initial_state": context["state"], "series": series})
    def rainfall_totals(rows):
        return {column: round(sum(row[column] for row in rows[anchor + 1:anchor + 25]), 6)
                for column in BASIN_RAINFALL_COLUMNS.values()}
    result = {
        "status": "running", "applied": False, "scenario_id": scenario_id,
        "workspace_id": active_workspace_id(), "baseline_forecast_id": metadata["forecast_id"],
        "t0": context["t0"], "t1": offset_time_iso(context["t0"], time_h), "time_h": time_h,
        "valid_to": metadata["valid_to"], "horizon_hours": 24, "target_type": target_type,
        "assumption": assumption, "rainfall_source": source,
        "rainfall_24h_mm": {"baseline": rainfall_totals(original_inputs), "candidate": rainfall_totals(inputs)},
        "dispatch_settings": settings,
        "reservoir_safety": {
            "baseline": reservoir_safety(context["t0"], context["state"], context["baseline_series"][1:]),
            "candidate": reservoir_safety(context["t0"], context["state"], series),
        },
        "warnings": dispatch["warnings"], "report_path": rel(folder / "report.json"),
        "basis": "固定原预测 t0 水库状态和调度模式，调整未来分区降水并重新计算产流、未来调度和四边界水动力预测；独立假设场景，未应用。",
        "limitations": ["降雨产流采用现有演示模型，未作水文率定。", "水动力模型沿用边界历史特征构造，无显式初始水深场接口。"],
        "stage": "hydrodynamic_model",
    }
    _save(folder / "report.json", result)
    try:
        model = run_cnn_v2_forecast(candidate_input, folder / "max_depth.csv", work_dir=folder / "cnn_work")
        if model.get("error") or model.get("status") == "failed":
            raise ValueError(f"降水假设场景水动力模型预测失败：{model.get('error') or '计算失败'}")
        candidate_array, candidate_steps = _load_depth_series(folder)
        if candidate_steps != steps or candidate_array.shape != baseline_array.shape:
            raise ValueError("假设场景预测与原预测的网格或时间切片不一致")
        if 0 in steps and not np.allclose(candidate_array[steps.index(0)], baseline_array[steps.index(0)], rtol=0, atol=1e-5):
            raise ValueError("假设场景改变了 t0 水深，请核查模型与原预测的输入一致性")
        result["stage"] = "impact_analysis"
        _save(folder / "report.json", result)
        comparisons, details = {}, {}
        candidate_metadata = {**metadata, "forecast_id": scenario_id}
        for label, hour, old, new in (
            ("selected_time", time_h, baseline_array[index], candidate_array[index]),
            ("window_envelope", None, baseline_array.max(axis=0), candidate_array.max(axis=0)),
        ):
            before = _impact(resolver, old, metadata, hour, target_type, object_ids)
            after = _impact(resolver, new, candidate_metadata, hour, target_type, object_ids)
            details[label] = {"baseline": before, "candidate": after}
            comparisons[label] = {**_comparison(before, after),
                                  "inundation": {"baseline": _inundation(old), "candidate": _inundation(new)}}
        _save(folder / "impact_details.json", details)
        result.update(status="completed", stage="completed", comparison=comparisons)
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status="failed", error=str(error))
    _save(folder / "report.json", result)
    return result
