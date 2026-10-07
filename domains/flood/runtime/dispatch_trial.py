"""Compare a reservoir dispatch trial with one immutable 24-hour forecast.

Trials own their model files and reports. They never replace the live forecast,
the selected playback source, or the active reservoir plan.
"""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import json
import math
from pathlib import Path
import re
import uuid

import numpy as np

from . import reservoir_engine as engine
from .cnn_v2 import run_cnn_v2_forecast
from .common import PROJECT_DIR, rel
from .forecast_query import forecast_cells_from_hydrodynamic_mesh
from .hydrodynamic_grid import forecast_metadata, offset_time_iso
from .impact_analysis import analyze_inundation_impacts, resolve_target_types
from .impact_scope import ImpactScope
from .reservoir_dispatch import (
    DISPATCH_DATA_DIR, DispatchSettings, dispatch_model_signature,
    parse_dispatch_settings, simulate_reservoir_dispatch,
)
from .workspace import active_workspace_id, workspace_dir


TRIAL_FIELDS = {
    "mode", "target_outflow_m3s", "target_level_m", "normal_release_m3s",
    "outlet_capacity_m3s", "max_release_m3s",
}


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("试算所需的预测快照格式无效")
    return value


def _load_basis(forecast_id: str) -> tuple[dict, dict, dict]:
    if forecast_id not in {"latest", "forecast_latest"} and not re.fullmatch(r"v\d+", forecast_id):
        raise ValueError("必须使用已生成的预测版本进行调度试算")
    metadata = forecast_metadata(forecast_id)
    if metadata.get("status") != "completed" or metadata.get("workspace_id") != active_workspace_id():
        raise ValueError("该工作空间没有对应的已完成预测")
    version = str(metadata.get("forecast_id") or "")
    if not re.fullmatch(r"v\d+", version):
        raise ValueError("预测版本缺失，无法固定试算起点")
    summary = metadata.get("boundary_flow") or {}
    if isinstance(summary, str):
        summary = json.loads(summary)
    path = (PROJECT_DIR / str(summary.get("input_path") or "")).resolve()
    input_root = (workspace_dir() / "boundary_flows" / "forecast_inputs").resolve()
    if not path.is_relative_to(input_root) or not path.is_file():
        raise ValueError("该预测缺少原始输入快照，请重新生成预测后试算")
    snapshot = _read_json(path)
    if snapshot.get("boundary_flow_id") != metadata.get("forecast_input_id"):
        raise ValueError("预测与输入快照不匹配，无法比较调度方案")
    context = snapshot.get("reservoir_dispatch_context") or {}
    if not context or context.get("t0") != metadata.get("valid_from"):
        raise ValueError("该预测缺少 t0 水库续算状态，请重新生成预测后试算")
    if context.get("model_signature") != dispatch_model_signature():
        raise ValueError("水库模型或参数在原预测后已变化，请重新生成预测后试算")
    t0 = datetime.fromisoformat(context["t0"])
    if (datetime.fromisoformat(metadata["valid_to"]) - t0).total_seconds() != 24 * 3600:
        raise ValueError("原预测有效期不是 t0 起的完整24小时")
    future = context.get("future_inflows") or []
    baseline = context.get("baseline_series") or []
    if len(future) < 24 or len(baseline) != 25:
        raise ValueError("试算需要完整的 t0 至未来24小时输入")
    for index, row in enumerate(future, 1):
        if (datetime.fromisoformat(row["valid_time"]) - t0).total_seconds() != index * 3600:
            raise ValueError("原预测水库入流序列不是连续小时数据")
    for index, row in enumerate(baseline):
        if (datetime.fromisoformat(row["valid_time"]) - t0).total_seconds() != index * 3600:
            raise ValueError("原预测调度过程与 t0 不一致")
    for key in ("interval1", "interval2", "tonggu", "upstream"):
        series = snapshot["summary"]["boundaries"][key]["series"]
        if [point["time_h"] for point in series] != list(range(25)):
            raise ValueError("原预测必须包含 t0 至 +24h 的四边界流量")
    return metadata, snapshot, context


def _settings(context: dict) -> DispatchSettings:
    return parse_dispatch_settings({
        key: value for key, value in context["settings"].items()
        if key in DispatchSettings.__dataclass_fields__
    })


def get_longtan_dispatch_plan(forecast_id: str = "latest") -> dict:
    metadata, _, context = _load_basis(forecast_id)
    return {
        "status": "completed", "forecast_id": metadata["forecast_id"],
        "t0": context["t0"], "valid_to": metadata["valid_to"],
        "settings": _settings(context).public(),
        "reservoir_state": context["state"],
        "thresholds": context["baseline_series"][0]["thresholds"],
        "adjustable_fields": sorted(TRIAL_FIELDS),
        "scope": "所选预测在 t0 执行的方案；候选方案只试算其后24小时，不应用。",
    }


def _candidate(context: dict, changes: dict) -> tuple[dict, list[dict]]:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("请提供需要试算的调度模式或参数")
    unknown = set(changes) - TRIAL_FIELDS
    if unknown:
        raise ValueError(f"不能调整 {', '.join(sorted(unknown))}；t0 水库状态和预见期沿用原预测")
    baseline = _settings(context)
    config = parse_dispatch_settings({**asdict(baseline), **changes})
    state = context["state"]
    inputs = deepcopy(context["future_inflows"])
    for row in inputs:
        for target in ("target_outflow_m3s", "target_level_m"):
            # A requested scenario-wide target replaces the original CSV targets.
            if target in changes or config.mode != baseline.mode:
                row[target] = None
    result = simulate_reservoir_dispatch(
        inputs, settings=config, dt_hours=1,
        initial_level_m=state["level_m"],
        initial_storage_1e4m3=state["storage_1e4m3"],
        input_peak_inflow_m3s=state["input_peak_inflow_m3s"],
    )
    # Keep the original rule look-ahead tail, but evaluate/output only 24 hours.
    return result, result["series"][:24]


def reservoir_safety(t0: str, state: dict, series: list[dict]) -> dict:
    points = [(t0, state["level_m"]), *[(row["valid_time"], row["end_level_m"]) for row in series]]
    thresholds = series[0]["thresholds"]
    maximum = max(points, key=lambda point: point[1])
    check = thresholds["check_flood_level_m"]
    exceeded = [time for time, level in points if level > check + engine.LEVEL_TOLERANCE]
    curve = engine.load_curve(DISPATCH_DATA_DIR / "storage_capacity_curve.dat")
    extrapolated = any(level < curve.x[0] or level > curve.x[-1] for _, level in points)
    status = "failed" if exceeded else "unknown" if extrapolated else "passed"
    return {
        "status": status, "passed": True if status == "passed" else False if status == "failed" else None,
        "max_level_m": round(maximum[1], 6), "peak_at": maximum[0],
        "final_level_m": series[-1]["end_level_m"],
        "check_flood_level_m": check, "margin_to_check_m": round(check - maximum[1], 6),
        "design_level_exceeded": maximum[1] > thresholds["design_flood_level_m"] + engine.LEVEL_TOLERANCE,
        "first_check_exceeded_at": exceeded[0] if exceeded else None,
        "actual_level_extrapolated": extrapolated,
        "criterion": "在 t0 至 +24h 的演算时段端点校核库水位不超过校核洪水位；设计水位超限另列提示。",
        "basis": "给定来水与能力假设下的水库模型校核，不表示工程安全保证。",
    }


def _candidate_boundary(snapshot: dict, series: list[dict], trial_id: str) -> dict:
    candidate = deepcopy(snapshot)
    candidate.pop("reservoir_dispatch_context", None)
    summary = candidate["summary"]
    summary.pop("input_path", None)
    summary["boundary_flow_id"] = trial_id
    candidate["boundary_flow_id"] = trial_id
    upstream = summary["boundaries"]["upstream"]
    for point, dispatch in zip(upstream["series"][1:], series):
        point["flow_m3s"] = dispatch["release_m3s"]
    flows = [point["flow_m3s"] for point in upstream["series"]]
    upstream.update(peak_flow_m3s=max(flows), mean_flow_m3s=sum(flows) / len(flows),
                    first_flow_m3s=flows[0], last_flow_m3s=flows[-1])
    return candidate


def _load_depth_series(folder: Path) -> tuple[np.ndarray, list[float]]:
    steps = _read_json(folder / "time_steps.json").get("time_steps_h") or []
    array = np.load(folder / "depth_series.npy", mmap_mode="r", allow_pickle=False)
    if (array.ndim != 2 or len(steps) != array.shape[0] or not array.shape[1]
            or not steps or steps[0] < 0 or steps[-1] != 24
            or any(not math.isfinite(value) for value in steps)
            or any(right <= left for left, right in zip(steps, steps[1:]))):
        raise ValueError("预测水深序列缺失或未完整覆盖未来24小时")
    for row in array:
        if not np.isfinite(row).all() or (row < 0).any():
            raise ValueError("预测包含无效水深，无法判断道路是否受淹")
    return array, steps


def _impact(resolver, values, metadata: dict, time_h: float | None,
            target_type: str, object_ids: list[str] | None) -> dict:
    depths = {int(index) + 1: float(values[index]) for index in np.flatnonzero(values > 0)}
    cells = forecast_cells_from_hydrodynamic_mesh(
        depths, metadata["generated_at"], time_h, metadata["forecast_id"],
    )
    if len(cells) != len(depths):
        raise ValueError("部分预测网格缺少空间几何，无法完成影响比较")
    result = analyze_inundation_impacts(
        resolver, metadata["forecast_id"], target_type=target_type, time_h=time_h,
        object_ids=object_ids, forecast_cells=cells,
        time_context={
            "forecast_time": metadata["valid_from"], "valid_from": metadata["valid_from"],
            "valid_to": metadata["valid_to"],
            "analysis_time_at": offset_time_iso(metadata["valid_from"], time_h),
        },
    )
    if result["status"] == "no_forecast_cells":
        # The arrays above were validated: this is a computed dry result.
        result["status"] = "completed"
    if result["status"] not in {"completed", "partial"}:
        raise ValueError(result.get("error") or "无法分析指定对象范围")
    return result


def _comparison(baseline: dict, candidate: dict) -> dict:
    before = {(row["object_type"], row["object_id"]): row for row in baseline["impacts"]}
    after = {(row["object_type"], row["object_id"]): row for row in candidate["impacts"]}
    removed, added = before.keys() - after.keys(), after.keys() - before.keys()
    complete = baseline["status"] == candidate["status"] == "completed"
    surface_unverified = any(
        row.get("impact_status") == "structure_overlap_unverified"
        or row.get("structure_unverified_segment_count", 0) > 0
        or row.get("object_type") == "Bridge"
        for row in [*before.values(), *after.values()]
    )
    def summary(result):
        rows = result["impacts"]
        segments = [segment for row in rows for segment in row.get("segment_impacts", [])]
        # Shared segments belong to multiple routes; count their length once.
        length_rows = {row["object_id"]: row for row in segments}.values() if segments else rows
        return {"affected_count": result["total_impacts"], "status": result["status"],
                "nearby_count": result.get("total_nearby", 0),
                "max_impact_depth_m": max((row.get("depth_m", 0) for row in rows), default=0),
                "overlap_length_m": round(sum(row.get("overlap_length_m", 0) for row in length_rows), 3),
                "unassessed_count": len((result.get("linear_analysis") or {}).get("unassessed_objects", []))}
    def samples(keys, source):
        return [{"object_type": key[0], "object_id": key[1], "name": source[key].get("name")}
                for key in sorted(keys)[:20]]
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, {}), after.get(key, {})
        changes.append({
            "object_type": key[0], "object_id": key[1], "name": (new or old).get("name"),
            "baseline_depth_m": old.get("depth_m", 0), "candidate_depth_m": new.get("depth_m", 0),
            "baseline_overlap_length_m": old.get("overlap_length_m", 0),
            "candidate_overlap_length_m": new.get("overlap_length_m", 0),
            "baseline_affected_segment_count": old.get("affected_segment_count"),
            "candidate_affected_segment_count": new.get("affected_segment_count"),
            "baseline_assessment": old.get("impact_status"),
            "candidate_assessment": new.get("impact_status"),
            "surface_elevation_unverified": (
                (new or old).get("impact_status") == "structure_overlap_unverified"
                or (new or old).get("structure_unverified_segment_count", 0) > 0
            ),
        })
    scope = baseline.get("analysis_scope") or {}
    return {
        "complete": complete, "analysis_scope": {
            "mode": scope.get("mode"), "matched_count": scope.get("matched_count"),
            "target_type": baseline.get("target_type"),
        },
        "surface_elevation_unverified": surface_unverified,
        "baseline": summary(baseline), "candidate": summary(candidate),
        "all_baseline_impacts_avoided": bool(before) and not after if complete and not surface_unverified else None,
        "removed_count": len(removed), "new_count": len(added),
        "removed_samples": samples(removed, before), "new_samples": samples(added, after),
        "object_changes": changes[:20], "object_changes_total": len(changes),
        "samples_truncated": len(changes) > 20,
        "note": "数量为完整所选范围；明细最多展示20项。邻近积水不计受淹，桥隧路面高程未知仍需核查。",
    }


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _dispatch_summary(series: list[dict]) -> dict:
    return {
        "peak_actual_release_m3s": max(row["release_m3s"] for row in series),
        "mean_actual_release_m3s": round(sum(row["release_m3s"] for row in series) / len(series), 6),
        "control_status_counts": dict(Counter(row["control_status"] for row in series)),
        "series": [{key: row[key] for key in (
            "valid_time", "release_m3s", "end_level_m", "state", "control_status", "control_status_label",
        )} for row in series],
    }


def simulate_longtan_dispatch(resolver, settings: dict, forecast_id: str = "latest",
                              time_h: float | None = None, target_type: str = "Road",
                              object_ids: list[str] | None = None) -> dict:
    metadata, snapshot, context = _load_basis(forecast_id)
    target_types = resolve_target_types(target_type)
    if not target_types:
        raise ValueError("不支持的影响对象类型")
    ImpactScope(resolver, target_types, object_ids, None)  # Validate before invoking CNN.
    baseline_dir = workspace_dir() / "forecasts" / metadata["forecast_id"]
    baseline_array, steps = _load_depth_series(baseline_dir)
    if time_h in (None, ""):
        time_h = 0.0
    if isinstance(time_h, bool) or not math.isfinite(float(time_h)):
        raise ValueError("目标预测时刻必须为有效小时偏移")
    matches = [index for index, hour in enumerate(steps) if math.isclose(hour, float(time_h), abs_tol=1e-6)]
    if not matches:
        raise ValueError("原预测没有目标时刻的精确切片，不能改用其他时刻或最大包络")
    index = matches[0]
    time_h = steps[index]
    dispatch, series = _candidate(context, settings)
    trial_id = f"dispatch_trial_{uuid.uuid4().hex}"
    folder = workspace_dir() / "dispatch_trials" / trial_id
    folder.mkdir(parents=True)
    candidate_input = _candidate_boundary(snapshot, series, trial_id)
    candidate_input["summary"]["reservoir_dispatch_settings"] = dispatch["settings"]
    _save(folder / "input.json", candidate_input)
    _save(folder / "reservoir.json", {"settings": dispatch["settings"], "series": series})
    result = {
        "status": "running", "applied": False, "trial_id": trial_id,
        "workspace_id": active_workspace_id(), "baseline_forecast_id": metadata["forecast_id"],
        "t0": context["t0"], "t1": offset_time_iso(context["t0"], time_h),
        "time_h": time_h, "valid_to": metadata["valid_to"], "horizon_hours": 24,
        "target_type": target_type,
        "baseline_settings": _settings(context).public(), "candidate_settings": dispatch["settings"],
        "reservoir_safety": {
            "baseline": reservoir_safety(context["t0"], context["state"], context["baseline_series"][1:]),
            "candidate": reservoir_safety(context["t0"], context["state"], series),
        },
        "warnings": dispatch["warnings"], "report_path": rel(folder / "report.json"),
        "dispatch": {
            "baseline": _dispatch_summary(context["baseline_series"][1:]),
            "candidate": _dispatch_summary(series),
            "changed_period_count": sum(
                old["release_m3s"] != new["release_m3s"]
                for old, new in zip(context["baseline_series"][1:], series)
            ),
        },
        "basis": "沿用原预测 t0、来水、其他三个边界和CNN时间基准，仅调整 t0 之后的水库下泄；结果为试算。",
        "cnn_initialization": "与原预测相同的边界历史特征构造，无显式初始水深场接口。",
        "stage": "cnn",
    }
    _save(folder / "report.json", result)
    try:
        cnn = run_cnn_v2_forecast(candidate_input, folder / "max_depth.csv", work_dir=folder / "cnn_work")
        if cnn.get("error"):
            raise ValueError(f"候选方案淹没预测失败：{cnn['error']}")
        candidate_array, candidate_steps = _load_depth_series(folder)
        if candidate_steps != steps or candidate_array.shape != baseline_array.shape:
            raise ValueError("候选预测与原预测的网格或时间切片不一致")
        if 0 in steps and not np.allclose(candidate_array[steps.index(0)], baseline_array[steps.index(0)], atol=1e-5):
            raise ValueError("候选方案改变了 t0 水深，请核查模型与原预测的输入一致性")
        result["stage"] = "impact_analysis"
        _save(folder / "report.json", result)
        comparisons, details = {}, {}
        candidate_metadata = {**metadata, "forecast_id": trial_id}
        for label, hour, old, new in (
            ("selected_time", time_h, baseline_array[index], candidate_array[index]),
            ("window_envelope", None, baseline_array.max(axis=0), candidate_array.max(axis=0)),
        ):
            before = _impact(resolver, old, metadata, hour, target_type, object_ids)
            after = _impact(resolver, new, candidate_metadata, hour, target_type, object_ids)
            details[label] = {"baseline": before, "candidate": after}
            comparisons[label] = _comparison(before, after)
        _save(folder / "impact_details.json", details)
        result.update(status="completed", stage="completed", comparison=comparisons)
        checks = [result["reservoir_safety"]["candidate"]["passed"],
                  comparisons["selected_time"]["all_baseline_impacts_avoided"],
                  comparisons["window_envelope"]["all_baseline_impacts_avoided"]]
        result["candidate_satisfies_objective"] = False if False in checks else None if None in checks else True
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status="failed", error=str(error), candidate_satisfies_objective=None)
    _save(folder / "report.json", result)
    return result
