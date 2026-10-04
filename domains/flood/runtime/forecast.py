from __future__ import annotations

import json
import math
import csv
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cnn_v2 import GRID_PATH, run_cnn_v2_forecast
from .common import id_field, rel
from .hydrodynamic_grid import MESH_DB_PATH
from .boundary_flow import read_latest_forecast_input
from .workspace import WORKSPACES, active_workspace_id, workspace_dir, workspace_scope
from . import forecast_storage as _forecast_storage
from .forecast_storage import (
    read_jsonl as _read_jsonl,
    write_jsonl as _write_jsonl,
)
from .forecast_geometry import (
    build_cell_spatial_index,
    nearest_cell,
    row_point,
    sampled_geometry_points,
)
from .forecast_constants import FORECAST_SCHEMA_VERSION, LATEST_FORECAST_ID
from .forecast_query import (
    clear_forecast_cell_cache,
    forecast_cell_summary_from_hydrodynamic_mesh,
    query_forecast_cells,
    read_hydrodynamic_depth_csv,
)


_FORECAST_RUN_LOCKS_LOCK = threading.Lock()
_FORECAST_RUN_LOCKS: dict[str, threading.RLock] = {}


def forecast_dir(*, create: bool = False) -> Path:
    return _forecast_storage.forecast_dir(create=create)


def forecast_runs_path() -> Path:
    return _forecast_storage.forecast_runs_path()


def legacy_forecast_runs_path() -> Path:
    return _forecast_storage.legacy_forecast_runs_path()


def forecast_pointer_path() -> Path:
    return _forecast_storage.forecast_pointer_path()


def forecast_cycle_path() -> Path:
    return _forecast_storage.forecast_cycle_path()


def hydrodynamic_forecast_depth_path() -> Path:
    return _forecast_storage.hydrodynamic_forecast_depth_path()


def hydrodynamic_forecast_series_path() -> Path:
    return _forecast_storage.hydrodynamic_forecast_series_path()


def hydrodynamic_forecast_time_steps_path() -> Path:
    return _forecast_storage.hydrodynamic_forecast_time_steps_path()


# Compatibility wrappers keep the historical ``forecast`` module seams
# patchable while the actual file-format implementation lives in
# ``forecast_storage``.
def read_jsonl(path: Path) -> list[dict]:
    return _read_jsonl(path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    _write_jsonl(path, rows)


def read_forecast_runs() -> list[dict]:
    rows = read_jsonl(forecast_runs_path())
    return rows or read_jsonl(legacy_forecast_runs_path())


def run_flood_forecast(resolver, forecast_id: str = "latest",
                       force: bool = False) -> dict[str, Any]:
    requested_id = str(forecast_id or "latest")
    if requested_id not in {"latest", LATEST_FORECAST_ID}:
        stored = next(
            (
                row for row in read_forecast_runs()
                if row.get("forecast_id") == requested_id
                or row.get("forecast_version") == requested_id
            ),
            None,
        )
        return (
            {"forecast": stored}
            if stored
            else {"error": "forecast not found", "forecast_id": requested_id}
        )
    run = ensure_latest_forecast(resolver, force=force)
    return {"forecast": run}


def assess_flood_emergency(resolver, refresh: bool = False) -> dict[str, Any]:
    """One assessment of an existing forecast; does not start playback or run CNN."""
    rows = read_forecast_runs()
    if not rows or rows[-1].get("status") != "completed":
        return {"status": "forecast_unavailable", "error": "当前轮次没有已完成预测，无法进行单次应急研判。"}
    forecast = rows[-1]
    if not refresh:
        cached = read_cached_emergency_cycle(forecast)
        if cached and cached.get("assessment_mode") == "single":
            return cached

    cells = query_forecast_cells({"forecast_id": LATEST_FORECAST_ID})
    evacuation_unit_impacts = impacted_evacuation_units(resolver, cells)
    road_impacts = impacted_linear_objects(resolver, cells, "Road", max_items=8)
    route_impacts = impacted_linear_objects(resolver, cells, "EvacuationRoute", max_items=6)
    warning = warning_from_forecast(
        forecast, evacuation_unit_impacts, road_impacts,
    )
    recommendations = emergency_recommendations(
        warning, evacuation_unit_impacts, road_impacts, route_impacts,
    )
    result = {
        "schema_version": FORECAST_SCHEMA_VERSION,
        "cycle_id": f"cycle_{LATEST_FORECAST_ID}",
        "status": "completed",
        "assessment_mode": "single",
        "analysis_view": "envelope",
        "continuous": False,
        "executed_actions": [],
        "stage": "assess_existing_forecast",
        "observations": hydrology_inputs_from_forecast(forecast),
        "forecast": forecast,
        "warning": warning,
        "evacuation_unit_impacts": evacuation_unit_impacts,
        "road_impacts": road_impacts,
        "route_impacts": route_impacts,
        "recommendations": recommendations,
    }
    write_cached_emergency_cycle(result)
    return result










def ensure_latest_forecast(resolver, force: bool = False) -> dict[str, Any]:
    if not active_workspace_id():
        WORKSPACES.create()
    workspace_id = active_workspace_id()
    if not workspace_id:
        raise RuntimeError("failed to create forecast workspace")
    with forecast_run_lock(workspace_id):
        with workspace_scope(workspace_id):
            return ensure_latest_forecast_locked(resolver, force=force)


def forecast_run_lock(workspace_id: str) -> threading.RLock:
    with _FORECAST_RUN_LOCKS_LOCK:
        lock = _FORECAST_RUN_LOCKS.get(workspace_id)
        if lock is None:
            lock = threading.RLock()
            _FORECAST_RUN_LOCKS[workspace_id] = lock
        return lock


def ensure_latest_forecast_locked(resolver, force: bool = False) -> dict[str, Any]:
    rows = read_forecast_runs()
    if not force and rows:
        latest_boundary_flow = read_latest_forecast_input()
        if (
            rows
            and rows[-1].get("workspace_id") == active_workspace_id()
            and rows[-1].get("schema_version") == FORECAST_SCHEMA_VERSION
            and cached_forecast_matches_input(rows[-1], latest_boundary_flow)
            and cached_forecast_outputs_available(rows[-1])
        ):
            return rows[-1]

    forecast_dir(create=True).mkdir(parents=True, exist_ok=True)
    run = generate_forecast(resolver)
    run = finalize_forecast_run(run, rows)
    write_jsonl(forecast_runs_path(), [*mark_forecasts_not_latest(rows), run])
    write_forecast_pointer(run)
    clear_forecast_cell_cache()
    clear_cached_cycle()
    clear_cached_geojson()
    return run


def finalize_forecast_run(
    run: dict[str, Any],
    existing_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    sequence = max(
        [
            int(item.get("forecast_sequence") or index)
            for index, item in enumerate(existing_runs, 1)
        ],
        default=0,
    ) + 1
    version = f"v{sequence:03d}"
    boundary_flow = _json_object(run.get("boundary_flow"))
    trigger = _json_object(run.get("forecast_trigger"))
    rainfall_series = run.get("rainfall_series")
    if not isinstance(rainfall_series, list):
        rainfall_series = boundary_flow.get("rainfall_series")
    finalized = {
        **run,
        "forecast_id": version,
        "forecast_version": version,
        "forecast_sequence": sequence,
        "workspace_id": active_workspace_id(),
        "forecast_time": (
            boundary_flow.get("simulation_time")
            or boundary_flow.get("triggered_at")
            or boundary_flow.get("observed_through")
            or run.get("generated_at")
        ),
        "valid_from": boundary_flow.get("window_start"),
        "valid_to": boundary_flow.get("window_end"),
        "rainfall_series": (
            rainfall_series if isinstance(rainfall_series, list) else []
        ),
        "trigger_reason": trigger.get("reason"),
        "is_latest": True,
    }
    archive_dir = workspace_dir() / "forecasts" / version
    archive_dir.mkdir(parents=True, exist_ok=True)
    output_paths = {
        "hydrodynamic_depth_path": _archive_forecast_output(
            hydrodynamic_forecast_depth_path(), archive_dir,
        ),
        "hydrodynamic_series_path": _archive_forecast_output(
            hydrodynamic_forecast_series_path(), archive_dir,
        ),
        "hydrodynamic_time_steps_path": _archive_forecast_output(
            hydrodynamic_forecast_time_steps_path(), archive_dir,
        ),
    }
    finalized.update(output_paths)
    finalized["data_path"] = output_paths["hydrodynamic_depth_path"]
    cnn_result = _json_object(finalized.get("cnn_v2"))
    if cnn_result:
        cnn_result.update(output_paths)
        finalized["cnn_v2"] = json.dumps(cnn_result, ensure_ascii=False)
    (archive_dir / "forecast.json").write_text(
        json.dumps(finalized, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return finalized


def _archive_forecast_output(source: Path, archive_dir: Path) -> str:
    if not source.exists():
        return ""
    target = archive_dir / source.name
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return rel(target)


def mark_forecasts_not_latest(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [{**row, "is_latest": False} for row in rows]


def write_forecast_pointer(run: dict[str, Any]) -> None:
    path = forecast_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "forecast_id": run.get("forecast_id"),
            "forecast_version": run.get("forecast_version"),
            "generated_at": run.get("generated_at"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def cached_forecast_matches_input(forecast: dict[str, Any],
                                  boundary_flow: dict[str, Any] | None) -> bool:
    if not boundary_flow:
        return not forecast.get("boundary_flow")
    expected_id = str((boundary_flow.get("summary") or {}).get("boundary_flow_id") or "")
    if not expected_id:
        return False
    try:
        cached_boundary_flow = json.loads(str(forecast.get("boundary_flow") or "{}"))
    except json.JSONDecodeError:
        return False
    return str(cached_boundary_flow.get("boundary_flow_id") or "") == expected_id


def cached_forecast_outputs_available(forecast: dict[str, Any]) -> bool:
    if forecast.get("status") != "completed":
        return True
    if not hydrodynamic_forecast_depth_path().exists():
        return False
    if forecast.get("hydrodynamic_series_path"):
        return (
            hydrodynamic_forecast_series_path().exists()
            and hydrodynamic_forecast_time_steps_path().exists()
        )
    return True


def generate_forecast(resolver) -> dict[str, Any]:
    boundary_flow = read_latest_forecast_input()
    trigger = boundary_flow.get("forecast_trigger") if boundary_flow else None
    if not boundary_flow:
        generated_at = datetime.now(timezone.utc).isoformat()
        reset_hydrodynamic_forecast_outputs()
        run = {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "forecast_id": LATEST_FORECAST_ID,
            "name": "珊瑚河实时预测演算",
            "status": "skipped_no_boundary_flow",
            "model_name": "FLOOD_CNN_V2",
            "model_description": "缺少最新四边界流量过程线，本轮不运行 CNN_V2。",
            "generated_at": generated_at,
            "lead_time_h": 0.0,
            "mesh_source_id": "",
            "mesh_source_path": "",
            "hydrology_inputs": "{}",
            "forcing_index": 0.0,
            "forecast_cell_count": 0,
            "inundated_area_km2": 0.0,
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "boundary_flow": "{}",
            "forecast_trigger": "{}",
            "forecast_input_id": "",
            "data_path": rel(hydrodynamic_forecast_depth_path()),
            "cell_materialization": "on_demand",
            "hydrodynamic_depth_path": rel(hydrodynamic_forecast_depth_path()),
            "hydrodynamic_series_path": "",
            "hydrodynamic_time_steps_path": "",
        }
        return run
    hydrology_inputs = hydrology_inputs_from_boundary_flow(boundary_flow)
    forcing = forcing_index(hydrology_inputs)
    generated_at = datetime.now(timezone.utc).isoformat()
    cnn_result = run_cnn_v2_forecast(boundary_flow, hydrodynamic_forecast_depth_path())
    if cnn_result.get("error"):
        reset_hydrodynamic_forecast_outputs()
        run = {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "forecast_id": LATEST_FORECAST_ID,
            "name": "珊瑚河实时预测演算",
            "status": "failed",
            "model_name": "FLOOD_CNN_V2",
            "model_description": str(cnn_result.get("error") or "CNN_V2 prediction failed"),
            "generated_at": generated_at,
            "lead_time_h": 0.0,
            "mesh_source_id": "cnn_v2_gt",
            "mesh_source_path": rel(GRID_PATH),
            "hydrology_inputs": json.dumps(hydrology_inputs, ensure_ascii=False),
            "boundary_flow": json.dumps(boundary_flow.get("summary") if boundary_flow else {}, ensure_ascii=False),
            "forecast_trigger": json.dumps(trigger or {}, ensure_ascii=False),
            "forecast_input_id": str((boundary_flow.get("summary") or {}).get("boundary_flow_id") or ""),
            "forcing_index": round(forcing, 3),
            "forecast_cell_count": 0,
            "inundated_area_km2": 0.0,
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "data_path": rel(hydrodynamic_forecast_depth_path()),
            "cell_materialization": "on_demand",
            "hydrodynamic_depth_path": rel(hydrodynamic_forecast_depth_path()),
            "hydrodynamic_series_path": "",
            "hydrodynamic_time_steps_path": "",
            "error_detail": json.dumps(cnn_result, ensure_ascii=False),
        }
        return run

    depths = cnn_result.pop("_positive_depths", None)
    if not isinstance(depths, dict):
        depths = read_hydrodynamic_depth_csv(hydrodynamic_forecast_depth_path())
    cell_summary = forecast_cell_summary_from_hydrodynamic_mesh(depths)
    run = {
        "schema_version": FORECAST_SCHEMA_VERSION,
        "forecast_id": LATEST_FORECAST_ID,
        "name": "珊瑚河实时预测演算",
        "status": "completed",
        "model_name": cnn_result.get("model_name", "FLOOD_CNN_V2"),
        "model_description": cnn_result.get("model_description", "CNN_V2 水动力模型预测。"),
        "generated_at": generated_at,
        "lead_time_h": float((boundary_flow.get("summary") or {}).get("forecast_horizon_h") or 0),
        "mesh_source_id": "cnn_v2_gt",
        "mesh_source_path": rel(GRID_PATH),
        "hydrology_inputs": json.dumps(hydrology_inputs, ensure_ascii=False),
        "boundary_flow": json.dumps(boundary_flow.get("summary") if boundary_flow else {}, ensure_ascii=False),
        "forecast_trigger": json.dumps(trigger or {}, ensure_ascii=False),
        "forecast_input_id": str((boundary_flow.get("summary") or {}).get("boundary_flow_id") or ""),
        "forcing_index": round(forcing, 3),
        "forecast_cell_count": cell_summary["forecast_cell_count"],
        "inundated_area_km2": cell_summary["inundated_area_km2"],
        "max_depth_m": round(float(cnn_result.get("max_depth_m") or 0), 3),
        "mean_depth_m": round(float(cnn_result.get("mean_depth_m") or 0), 3),
        "data_path": rel(hydrodynamic_forecast_depth_path()),
        "cell_materialization": "on_demand",
        "hydrodynamic_depth_path": rel(hydrodynamic_forecast_depth_path()),
        "hydrodynamic_series_path": str(cnn_result.get("hydrodynamic_series_path") or ""),
        "hydrodynamic_time_steps_path": str(cnn_result.get("hydrodynamic_time_steps_path") or ""),
        "time_step_count": int(cnn_result.get("time_step_count") or 0),
        "time_steps_h": json.dumps(cnn_result.get("time_steps_h") or [], ensure_ascii=False),
        "cnn_v2": json.dumps(cnn_result, ensure_ascii=False),
    }
    return run


def hydrology_inputs_from_boundary_flow(boundary_flow: dict[str, Any] | None) -> dict[str, float]:
    summary = (boundary_flow or {}).get("summary") or {}
    boundaries = summary.get("boundaries") or {}
    legacy_current_index = max(0, int(summary.get("observed_point_count") or 1) - 1)
    current_index = 0 if summary.get("simulation_time") else legacy_current_index
    current_flows = [
        float((row.get("series") or [{}])[min(current_index, len(row.get("series") or [{}]) - 1)].get("flow_m3s") or 0)
        for row in boundaries.values()
    ]
    total_current = sum(current_flows)
    predicted_rainfall = float(
        summary.get("predicted_rainfall_24h_mm")
        or (summary.get("rainfall_total_mm") if summary.get("simulation_time") else 0)
        or 0
    )
    return {
        "observed_rainfall_6h_mm": float(summary.get("observed_rainfall_mm") or 0),
        "forecast_rainfall_3h_mm": float(summary.get("forecast_rainfall_mm") or 0),
        "predicted_rainfall_24h_mm": predicted_rainfall,
        "reservoir_water_level_m": float(summary.get("reservoir_level_m") or 0),
        "reservoir_flood_limit_level_m": 245.3,
        "reservoir_outflow_m3s": float(current_flows[-1] if current_flows else 0),
        "river_boundary_flow_m3s": total_current,
    }


def hydrology_inputs_from_forecast(forecast: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(forecast.get("hydrology_inputs") or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


















def write_hydrodynamic_depth_csv(depths: dict[int, float], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["cell_id", "max_depth"])
        writer.writeheader()
        for cell_id, depth in sorted(depths.items()):
            writer.writerow({"cell_id": cell_id, "max_depth": depth})


def reset_hydrodynamic_forecast_outputs() -> None:
    write_hydrodynamic_depth_csv({}, hydrodynamic_forecast_depth_path())
    for path in (hydrodynamic_forecast_series_path(), hydrodynamic_forecast_time_steps_path()):
        if path.exists():
            path.unlink()


def forcing_index(inputs: dict[str, float]) -> float:
    rain_term = inputs["observed_rainfall_6h_mm"] / 140.0 * 0.48
    forecast_term = inputs["forecast_rainfall_3h_mm"] / 70.0 * 0.28
    forecast_term += inputs.get("predicted_rainfall_24h_mm", 0.0) / 560.0 * 0.28
    reservoir_term = max(0.0, inputs["reservoir_water_level_m"] - inputs["reservoir_flood_limit_level_m"]) * 0.08
    outflow_term = inputs["reservoir_outflow_m3s"] / 650.0 * 0.18
    boundary_term = inputs["river_boundary_flow_m3s"] / 900.0 * 0.16
    return max(0.45, min(1.65, rain_term + forecast_term + reservoir_term + outflow_term + boundary_term))




def warning_from_forecast(forecast: dict[str, Any],
                          evacuation_unit_impacts: list[dict[str, Any]],
                          road_impacts: list[dict[str, Any]]) -> dict[str, Any]:
    max_depth = float(forecast.get("max_depth_m") or 0)
    area = float(forecast.get("inundated_area_km2") or 0)
    affected_population = sum(
        int(row.get("population") or 0) for row in evacuation_unit_impacts
    )
    if max_depth >= 1.8 or affected_population >= 200 or len(road_impacts) >= 5:
        level = "red"
    elif max_depth >= 1.2 or affected_population >= 50 or area >= 1.5:
        level = "orange"
    elif max_depth >= 0.6 or affected_population:
        level = "yellow"
    else:
        level = "blue"
    return {
        "warning_id": f"warning_{LATEST_FORECAST_ID}",
        "level": level,
        "title": f"珊瑚河洪水{level_name(level)}预警",
        "basis": (
            f"预测淹没面积 {area:.2f} km²，最大水深 {max_depth:.2f} m，"
            f"需关注转移单元 {len(evacuation_unit_impacts)} 个、道路对象 {len(road_impacts)} 个。"
        ),
        "affected_population": affected_population,
        "requires_human_approval": level in {"orange", "red"},
    }


def emergency_recommendations(warning: dict[str, Any],
                              evacuation_unit_impacts: list[dict[str, Any]],
                              road_impacts: list[dict[str, Any]],
                              route_impacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations = []
    for index, item in enumerate(evacuation_unit_impacts[:8], 1):
        recommendations.append({
            "recommendation_id": f"rec_evacuation_unit_{index}",
            "action_type": "evacuate",
            "priority": "immediate" if warning["level"] in {"orange", "red"} else "within_3h",
            "target_type": "EvacuationUnit",
            "target_id": item["evacuation_unit_id"],
            "message": f"组织 {item['town_name']}{item['name']} 转移 {item['population']} 人至 {item.get('destination_site_name') or item.get('destination_site_id') or '就近安置点'}。",
            "basis": f"预测最近淹没单元水深 {item['depth_m']:.2f} m，到达时间 {item['arrival_time_h']:.2f} h。",
            "requires_human_approval": True,
        })
    for index, item in enumerate(road_impacts[:5], 1):
        recommendations.append({
            "recommendation_id": f"rec_road_{index}",
            "action_type": "close_road",
            "priority": "within_1h",
            "target_type": "Road",
            "target_id": item["object_id"],
            "message": f"对 {item['name']} 近河低洼路段实施巡查和临时交通管控。",
            "basis": f"路线几何邻近预测淹没单元，最近水深 {item['depth_m']:.2f} m。",
            "requires_human_approval": True,
        })
    if route_impacts:
        recommendations.append({
            "recommendation_id": "rec_route_review",
            "action_type": "detour",
            "priority": "within_1h",
            "target_type": "EvacuationRoute",
            "target_id": ",".join(item["object_id"] for item in route_impacts[:5]),
            "message": "复核受预测淹没影响的转移路线，必要时启用备用绕行。",
            "basis": f"发现 {len(route_impacts)} 条转移路线邻近预测淹没单元。",
            "requires_human_approval": True,
        })
    return recommendations


def impacted_evacuation_units(
    resolver,
    cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cell_index = compact_cell_index(cells, min_depth=0.25)
    sites = {
        row.get("evacuation_site_id"): row
        for row in resolver.query("EvacuationSite")
    }
    destination_by_unit = {
        row.get("origin_unit_id"): row.get("destination_site_id")
        for row in resolver.query("EvacuationRoute")
        if row.get("origin_unit_id") and row.get("destination_site_id")
    }
    impacts = []
    for unit in resolver.query("EvacuationUnit"):
        point = row_point(unit)
        if not point:
            continue
        cell = nearest_cell(point, cell_index, max_distance_m=140)
        if not cell:
            continue
        destination_site_id = destination_by_unit.get(
            unit.get("evacuation_unit_id"), "",
        )
        destination_site = sites.get(destination_site_id)
        impacts.append({
            "evacuation_unit_id": unit.get("evacuation_unit_id", ""),
            "name": unit.get("name", ""),
            "town_name": unit.get("town_name", ""),
            "population": int(unit.get("population") or 0),
            "destination_site_id": destination_site_id,
            "destination_site_name": (
                destination_site.get("name") if destination_site else ""
            ),
            "depth_m": float(cell.get("depth_m") or 0),
            "velocity_mps": float(cell.get("velocity_mps") or 0),
            "arrival_time_h": float(cell.get("arrival_time_h") or 0),
            "distance_m": round(float(cell.get("_distance_m") or 0), 1),
        })
    return sorted(impacts, key=lambda row: (-row["depth_m"], row["arrival_time_h"]))


def impacted_linear_objects(resolver, cells: list[dict[str, Any]],
                            object_type: str, max_items: int) -> list[dict[str, Any]]:
    cell_index = compact_cell_index(cells, min_depth=0.35)
    impacts = []
    id_name = id_field(object_type)
    for row in resolver.query(object_type):
        points = sampled_geometry_points(row, max_points=16)
        if not points:
            continue
        matched = [nearest_cell(point, cell_index, max_distance_m=110) for point in points]
        matched = [item for item in matched if item]
        if not matched:
            continue
        deepest = max(matched, key=lambda item: float(item.get("depth_m") or 0))
        impacts.append({
            "object_type": object_type,
            "object_id": row.get(id_name, ""),
            "name": row.get("name") or row.get(id_name, ""),
            "depth_m": float(deepest.get("depth_m") or 0),
            "velocity_mps": float(deepest.get("velocity_mps") or 0),
            "arrival_time_h": float(deepest.get("arrival_time_h") or 0),
            "sample_hits": len(matched),
        })
    return sorted(impacts, key=lambda row: (-row["depth_m"], row["arrival_time_h"]))[:max_items]


def compact_cell_index(cells: list[dict[str, Any]], min_depth: float) -> dict[str, Any]:
    result = [
        row for row in cells
        if float(row.get("depth_m") or 0) >= min_depth and row.get("centroid_lon") and row.get("centroid_lat")
    ]
    if len(result) <= 7000:
        return build_cell_spatial_index(result)
    step = max(1, len(result) // 7000)
    return build_cell_spatial_index(result[::step])
























def level_name(level: str) -> str:
    return {
        "red": "红色",
        "orange": "橙色",
        "yellow": "黄色",
        "blue": "蓝色",
    }.get(level, level)


















def read_cached_emergency_cycle(forecast: dict[str, Any]) -> dict[str, Any] | None:
    if not forecast_cycle_path().exists():
        return None
    try:
        cached = json.loads(forecast_cycle_path().read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    cached_forecast = cached.get("forecast") or {}
    if (
        cached.get("schema_version") == FORECAST_SCHEMA_VERSION
        and cached_forecast.get("forecast_id") == forecast.get("forecast_id")
        and cached_forecast.get("generated_at") == forecast.get("generated_at")
    ):
        cached.pop("mappable", None)
        return cached
    return None


def write_cached_emergency_cycle(result: dict[str, Any]) -> None:
    forecast_cycle_path().parent.mkdir(parents=True, exist_ok=True)
    forecast_cycle_path().write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def clear_cached_cycle() -> None:
    if forecast_cycle_path().exists():
        forecast_cycle_path().unlink()


def clear_cached_geojson() -> None:
    cache_dir = workspace_dir(create=True) / "cache" / "geojson"
    if not cache_dir.exists():
        return
    for pattern in ("inundationforecastcell*.geojson", "forecastcell*.geojson"):
        for path in cache_dir.glob(pattern):
            path.unlink()
