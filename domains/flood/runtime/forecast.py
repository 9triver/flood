from __future__ import annotations

import json
import math
import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cnn_v2 import GRID_PATH, run_cnn_v2_forecast
from .common import DOMAIN_DATA_DIR, GENERATED_DIR, apply_filters, apply_order, apply_window, rel
from .hydrodynamic_grid import MESH_DB_PATH
from .mock_boundary_flow import evaluate_forecast_trigger, read_latest_boundary_flow


FORECAST_DIR = GENERATED_DIR / "forecast"
FORECAST_RUNS_PATH = FORECAST_DIR / "forecast_runs.jsonl"
FORECAST_CELLS_PATH = FORECAST_DIR / "forecast_cells_latest.jsonl"
FORECAST_CYCLE_PATH = FORECAST_DIR / "emergency_cycle_latest.json"
HYDRODYNAMIC_FORECAST_DEPTH_PATH = DOMAIN_DATA_DIR / "hydrodynamic" / "forecasts" / "latest" / "max_depth.csv"
SCENARIO_DEPTH_DIR = DOMAIN_DATA_DIR / "hydrodynamic" / "scenarios"
LATEST_FORECAST_ID = "forecast_latest"


MOCK_HYDROLOGY = {
    "observed_rainfall_6h_mm": 146.0,
    "forecast_rainfall_3h_mm": 58.0,
    "reservoir_water_level_m": 193.6,
    "reservoir_flood_limit_level_m": 191.2,
    "reservoir_outflow_m3s": 520.0,
    "river_boundary_flow_m3s": 780.0,
}


def run_flood_forecast(resolver, forecast_id: str = "latest",
                       force: bool = False) -> dict[str, Any]:
    run = ensure_latest_forecast(resolver, force=force)
    if forecast_id not in ("", "latest", LATEST_FORECAST_ID, run["forecast_id"]):
        return {"error": "forecast not found", "forecast_id": forecast_id}
    return {"forecast": run}


def run_emergency_cycle(resolver, force_forecast: bool = False,
                        force_analysis: bool = False) -> dict[str, Any]:
    forecast_result = run_flood_forecast(resolver, forecast_id="latest", force=force_forecast)
    if "error" in forecast_result:
        return forecast_result
    forecast = forecast_result["forecast"]
    if not force_analysis:
        cached = read_cached_emergency_cycle(forecast)
        if cached:
            return cached

    cells = query_forecast_cells(resolver, {"forecast_id": LATEST_FORECAST_ID})
    transfer_impacts = impacted_transfer_units(resolver, cells)
    road_impacts = impacted_linear_objects(resolver, cells, "Road", max_items=8)
    route_impacts = impacted_linear_objects(resolver, cells, "Route", max_items=6)
    warning = warning_from_forecast(forecast, transfer_impacts, road_impacts)
    recommendations = emergency_recommendations(warning, transfer_impacts, road_impacts, route_impacts)
    result = {
        "cycle_id": f"cycle_{LATEST_FORECAST_ID}",
        "status": "completed",
        "stage": "observe_forecast_warn_dispatch",
        "observations": MOCK_HYDROLOGY,
        "forecast": forecast,
        "warning": warning,
        "transfer_impacts": transfer_impacts,
        "road_impacts": road_impacts,
        "route_impacts": route_impacts,
        "recommendations": recommendations,
    }
    write_cached_emergency_cycle(result)
    return result


def query_forecast_runs(resolver, filters: dict[str, Any] | None = None,
                        limit: int | None = None,
                        order_by: str | None = None,
                        offset: int | None = None) -> list[dict]:
    ensure_latest_forecast(resolver)
    rows = read_jsonl(FORECAST_RUNS_PATH)
    rows = apply_filters(rows, normalize_forecast_filters(filters))
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def query_forecast_cells(resolver, filters: dict[str, Any] | None = None,
                         limit: int | None = None,
                         order_by: str | None = None,
                         offset: int | None = None) -> list[dict]:
    ensure_latest_forecast(resolver)
    rows = read_jsonl(FORECAST_CELLS_PATH)
    rows = apply_filters(rows, normalize_forecast_filters(filters))
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def count_forecast_runs(resolver, filters: dict[str, Any] | None = None) -> int:
    return len(query_forecast_runs(resolver, filters))


def count_forecast_cells(resolver, filters: dict[str, Any] | None = None) -> int:
    return len(query_forecast_cells(resolver, filters))


def ensure_latest_forecast(resolver, force: bool = False) -> dict[str, Any]:
    if not force and FORECAST_RUNS_PATH.exists() and FORECAST_CELLS_PATH.exists():
        rows = read_jsonl(FORECAST_RUNS_PATH)
        latest_boundary_flow = read_latest_boundary_flow()
        if rows and cached_forecast_matches_boundary_flow(rows[-1], latest_boundary_flow):
            return rows[-1]

    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    run, cells = generate_forecast(resolver)
    write_jsonl(FORECAST_RUNS_PATH, [run])
    write_jsonl(FORECAST_CELLS_PATH, cells)
    clear_cached_cycle()
    clear_cached_geojson()
    return run


def cached_forecast_matches_boundary_flow(forecast: dict[str, Any],
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


def generate_forecast(resolver) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    boundary_flow = read_latest_boundary_flow()
    trigger = evaluate_forecast_trigger(boundary_flow) if boundary_flow else None
    if not boundary_flow:
        generated_at = datetime.now(timezone.utc).isoformat()
        write_hydrodynamic_depth_csv({}, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
        run = {
            "forecast_id": LATEST_FORECAST_ID,
            "name": "珊瑚河实时预测演算",
            "status": "skipped_no_boundary_flow",
            "model_name": "FLOOD_CNN_V2",
            "model_description": "缺少最新四边界流量过程线，本轮不运行 CNN_V2。",
            "generated_at": generated_at,
            "lead_time_h": 0.0,
            "mesh_source_scenario_id": "",
            "mesh_source_path": "",
            "hydrology_inputs": "{}",
            "forcing_index": 0.0,
            "forecast_cell_count": 0,
            "inundated_area_km2": 0.0,
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "boundary_flow": "{}",
            "forecast_trigger": "{}",
            "data_path": rel(FORECAST_CELLS_PATH),
            "hydrodynamic_depth_path": rel(HYDRODYNAMIC_FORECAST_DEPTH_PATH),
        }
        return run, []
    if trigger and not trigger.get("should_run_forecast"):
        generated_at = datetime.now(timezone.utc).isoformat()
        write_hydrodynamic_depth_csv({}, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
        run = {
            "forecast_id": LATEST_FORECAST_ID,
            "name": "珊瑚河实时预测演算",
            "status": "skipped_dry_condition",
            "model_name": "shanhu_boundary_flow_gate",
            "model_description": "领域规则判断四边界流量未达到水动力模型触发条件，本轮不运行 CNN。",
            "generated_at": generated_at,
            "lead_time_h": 0.0,
            "mesh_source_scenario_id": "",
            "mesh_source_path": "",
            "hydrology_inputs": json.dumps(boundary_flow.get("summary") if boundary_flow else {}, ensure_ascii=False),
            "forcing_index": 0.0,
            "forecast_cell_count": 0,
            "inundated_area_km2": 0.0,
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "forecast_trigger": json.dumps(trigger, ensure_ascii=False),
            "data_path": rel(HYDRODYNAMIC_FORECAST_DEPTH_PATH),
        }
        return run, []

    hydrology_inputs = hydrology_inputs_from_boundary_flow(boundary_flow) if boundary_flow else MOCK_HYDROLOGY
    forcing = forcing_index(hydrology_inputs)
    generated_at = datetime.now(timezone.utc).isoformat()
    cnn_result = run_cnn_v2_forecast(boundary_flow, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
    if cnn_result.get("error"):
        write_hydrodynamic_depth_csv({}, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
        run = {
            "forecast_id": LATEST_FORECAST_ID,
            "name": "珊瑚河实时预测演算",
            "status": "failed",
            "model_name": "FLOOD_CNN_V2",
            "model_description": str(cnn_result.get("error") or "CNN_V2 prediction failed"),
            "generated_at": generated_at,
            "lead_time_h": 0.0,
            "mesh_source_scenario_id": "cnn_v2_gt",
            "mesh_source_path": rel(GRID_PATH),
            "hydrology_inputs": json.dumps(hydrology_inputs, ensure_ascii=False),
            "boundary_flow": json.dumps(boundary_flow.get("summary") if boundary_flow else {}, ensure_ascii=False),
            "forecast_trigger": json.dumps(trigger or {}, ensure_ascii=False),
            "forcing_index": round(forcing, 3),
            "forecast_cell_count": 0,
            "inundated_area_km2": 0.0,
            "max_depth_m": 0.0,
            "mean_depth_m": 0.0,
            "data_path": rel(FORECAST_CELLS_PATH),
            "hydrodynamic_depth_path": rel(HYDRODYNAMIC_FORECAST_DEPTH_PATH),
            "error_detail": json.dumps(cnn_result, ensure_ascii=False),
        }
        return run, []

    depths = read_hydrodynamic_depth_csv(HYDRODYNAMIC_FORECAST_DEPTH_PATH)
    cells = forecast_cells_from_hydrodynamic_mesh(depths, generated_at)

    total_area_km2 = sum(float(row.get("area_m2") or 0) for row in cells) / 1_000_000
    run = {
        "forecast_id": LATEST_FORECAST_ID,
        "name": "珊瑚河实时预测演算",
        "status": "completed",
        "model_name": cnn_result.get("model_name", "FLOOD_CNN_V2"),
        "model_description": cnn_result.get("model_description", "CNN_V2 水动力模型预测。"),
        "generated_at": generated_at,
        "lead_time_h": 3.0,
        "mesh_source_scenario_id": "cnn_v2_gt",
        "mesh_source_path": rel(GRID_PATH),
        "hydrology_inputs": json.dumps(hydrology_inputs, ensure_ascii=False),
        "boundary_flow": json.dumps(boundary_flow.get("summary") if boundary_flow else {}, ensure_ascii=False),
        "forecast_trigger": json.dumps(trigger or {}, ensure_ascii=False),
        "forcing_index": round(forcing, 3),
        "forecast_cell_count": int(cnn_result.get("flooded_count") or len(cells)),
        "inundated_area_km2": round(total_area_km2, 4),
        "max_depth_m": round(float(cnn_result.get("max_depth_m") or 0), 3),
        "mean_depth_m": round(float(cnn_result.get("mean_depth_m") or 0), 3),
        "data_path": rel(FORECAST_CELLS_PATH),
        "hydrodynamic_depth_path": rel(HYDRODYNAMIC_FORECAST_DEPTH_PATH),
        "cnn_v2": json.dumps(cnn_result, ensure_ascii=False),
    }
    return run, cells


def hydrology_inputs_from_boundary_flow(boundary_flow: dict[str, Any] | None) -> dict[str, float]:
    summary = (boundary_flow or {}).get("summary") or {}
    boundaries = summary.get("boundaries") or {}
    total_peak = sum(float(row.get("peak_flow_m3s") or 0) for row in boundaries.values())
    flow_index_value = float(summary.get("flow_index") or 0)
    return {
        "observed_rainfall_6h_mm": min(220.0, 42.0 + total_peak * 0.22),
        "forecast_rainfall_3h_mm": min(110.0, 18.0 + flow_index_value * 11.0),
        "reservoir_water_level_m": 190.8 + flow_index_value * 0.55,
        "reservoir_flood_limit_level_m": 191.2,
        "reservoir_outflow_m3s": float((boundaries.get("upstream") or {}).get("peak_flow_m3s") or 0) * 2.8,
        "river_boundary_flow_m3s": total_peak,
    }


def read_hydrodynamic_depth_csv(path: Path) -> dict[int, float]:
    if not path.exists():
        return {}
    depths: dict[int, float] = {}
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                cell_id = int(row["cell_id"])
                depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if depth > 0:
                depths[cell_id] = depth
    return depths


def forecast_cells_from_hydrodynamic_mesh(depths: dict[int, float],
                                          generated_at: str) -> list[dict[str, Any]]:
    if not MESH_DB_PATH.exists() or not depths:
        return []
    cells = []
    with sqlite3.connect(MESH_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select cell_id, lon1, lat1, lon2, lat2, lon3, lat3 from cells order by cell_id"
        )
        for index, row in enumerate(rows, 1):
            mesh_cell_id = int(row["cell_id"])
            depth_m = float(depths.get(mesh_cell_id) or 0)
            if depth_m < 0.04:
                continue
            coordinates = [
                [float(row["lon1"]), float(row["lat1"])],
                [float(row["lon2"]), float(row["lat2"])],
                [float(row["lon3"]), float(row["lat3"])],
                [float(row["lon1"]), float(row["lat1"])],
            ]
            centroid = (
                sum(point[0] for point in coordinates[:3]) / 3,
                sum(point[1] for point in coordinates[:3]) / 3,
            )
            area_m2 = triangle_area_m2(coordinates[:3])
            velocity = round(max(0.04, min(2.4, 0.10 + math.sqrt(depth_m) * 0.38)), 3)
            cells.append({
                "forecast_cell_id": f"{LATEST_FORECAST_ID}_{index}",
                "forecast_id": LATEST_FORECAST_ID,
                "model_name": "FLOOD_CNN_V2",
                "mesh_cell_id": str(mesh_cell_id),
                "mesh_source_scenario_id": "cnn_v2_gt",
                "lead_time_h": 3.0,
                "centroid_lon": round(centroid[0], 7),
                "centroid_lat": round(centroid[1], 7),
                "distance_to_river_m": 0,
                "river_along_ratio": 0,
                "ground_elevation_m": 0,
                "water_level_m": round(depth_m, 3),
                "depth_m": round(depth_m, 3),
                "velocity_mps": velocity,
                "arrival_time_h": 0,
                "recession_time_h": 0,
                "risk_level": risk_level(depth_m, velocity),
                "area_m2": round(area_m2, 3),
                "geometry_type": "Polygon",
                "geometry_crs": "EPSG:4326",
                "geometry": json.dumps({
                    "type": "Polygon",
                    "coordinates": [coordinates],
                }, ensure_ascii=False),
                "generated_at": generated_at,
            })
    return cells


def triangle_area_m2(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    ref_lat = sum(point[1] for point in points[:3]) / 3
    projected = [project((point[0], point[1]), ref_lat) for point in points[:3]]
    (x1, y1), (x2, y2), (x3, y3) = projected
    return abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2


def write_mock_hydrodynamic_depths(boundary_flow: dict[str, Any] | None,
                                   forcing: float) -> dict[str, Any]:
    summary = (boundary_flow or {}).get("summary") or {}
    scenario_id = str(summary.get("template_scenario_id") or "45050092hsfx0003")
    base_path = SCENARIO_DEPTH_DIR / f"{scenario_id}_max_depth.csv"
    if not base_path.exists():
        write_hydrodynamic_depth_csv({}, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
        return {"depth_count": 0, "flooded_count": 0, "max_depth_m": 0.0}
    flow_index_value = float(summary.get("flow_index") or 1.0)
    scale = max(0.05, min(2.2, 0.32 + forcing * 0.34 + flow_index_value * 0.08))
    depths: dict[int, float] = {}
    with base_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            adjusted = round(max(0.0, depth * scale), 6)
            if adjusted > 0:
                depths[int(row["cell_id"])] = adjusted
    write_hydrodynamic_depth_csv(depths, HYDRODYNAMIC_FORECAST_DEPTH_PATH)
    return {
        "depth_count": len(depths),
        "flooded_count": sum(1 for depth in depths.values() if depth > 0),
        "max_depth_m": max(depths.values(), default=0.0),
    }


def write_hydrodynamic_depth_csv(depths: dict[int, float], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["cell_id", "max_depth"])
        writer.writeheader()
        for cell_id, depth in sorted(depths.items()):
            writer.writerow({"cell_id": cell_id, "max_depth": depth})


def predicted_depth(base: dict[str, Any], distance_m: float,
                    along_ratio: float, forcing: float) -> float:
    ground = float(base.get("ground_elevation_m") or 0)
    terrain = max(0.0, min(1.0, (206.0 - ground) / 26.0)) if ground else 0.35
    channel_attenuation = math.exp(-distance_m / 540.0)
    along_wave = 0.72 + 0.28 * math.sin(math.pi * max(0.0, min(1.0, along_ratio)))
    hydraulic_head = 0.18 + 1.72 * forcing * channel_attenuation * along_wave + 0.42 * terrain
    lateral_loss = distance_m / 1250.0
    return round(max(0.0, hydraulic_head - lateral_loss), 3)


def forcing_index(inputs: dict[str, float]) -> float:
    rain_term = inputs["observed_rainfall_6h_mm"] / 140.0 * 0.48
    forecast_term = inputs["forecast_rainfall_3h_mm"] / 70.0 * 0.28
    reservoir_term = max(0.0, inputs["reservoir_water_level_m"] - inputs["reservoir_flood_limit_level_m"]) * 0.08
    outflow_term = inputs["reservoir_outflow_m3s"] / 650.0 * 0.18
    boundary_term = inputs["river_boundary_flow_m3s"] / 900.0 * 0.16
    return max(0.45, min(1.65, rain_term + forecast_term + reservoir_term + outflow_term + boundary_term))


def risk_level(depth_m: float, velocity_mps: float) -> str:
    if depth_m >= 1.6 or depth_m * velocity_mps >= 1.2:
        return "critical"
    if depth_m >= 0.9 or depth_m * velocity_mps >= 0.55:
        return "high"
    if depth_m >= 0.35:
        return "medium"
    return "low"


def warning_from_forecast(forecast: dict[str, Any],
                          transfer_impacts: list[dict[str, Any]],
                          road_impacts: list[dict[str, Any]]) -> dict[str, Any]:
    max_depth = float(forecast.get("max_depth_m") or 0)
    area = float(forecast.get("inundated_area_km2") or 0)
    affected_population = sum(int(row.get("population") or 0) for row in transfer_impacts)
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
            f"需关注转移对象 {len(transfer_impacts)} 个、道路对象 {len(road_impacts)} 个。"
        ),
        "affected_population": affected_population,
        "requires_human_approval": level in {"orange", "red"},
    }


def emergency_recommendations(warning: dict[str, Any],
                              transfer_impacts: list[dict[str, Any]],
                              road_impacts: list[dict[str, Any]],
                              route_impacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations = []
    for index, item in enumerate(transfer_impacts[:8], 1):
        recommendations.append({
            "recommendation_id": f"rec_transfer_{index}",
            "action_type": "evacuate",
            "priority": "immediate" if warning["level"] in {"orange", "red"} else "within_3h",
            "target_type": "Transfer",
            "target_id": item["transfer_id"],
            "message": f"组织 {item['town_name']}{item['name']} 转移 {item['population']} 人至 {item.get('place_name') or item.get('place_id') or '就近安置点'}。",
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
            "target_type": "Route",
            "target_id": ",".join(item["object_id"] for item in route_impacts[:5]),
            "message": "复核受预测淹没影响的转移路线，必要时启用备用绕行。",
            "basis": f"发现 {len(route_impacts)} 条转移路线邻近预测淹没单元。",
            "requires_human_approval": True,
        })
    return recommendations


def impacted_transfer_units(resolver, cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cell_index = compact_cell_index(cells, min_depth=0.25)
    places = {row.get("place_id"): row for row in resolver.query("Place")}
    impacts = []
    for transfer in resolver.query("Transfer"):
        point = row_point(transfer)
        if not point:
            continue
        cell = nearest_cell(point, cell_index, max_distance_m=140)
        if not cell:
            continue
        place = places.get(transfer.get("place_id"))
        impacts.append({
            "transfer_id": transfer.get("transfer_id", ""),
            "name": transfer.get("name", ""),
            "town_name": transfer.get("town_name", ""),
            "population": int(transfer.get("population") or 0),
            "place_id": transfer.get("place_id", ""),
            "place_name": place.get("name") if place else "",
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
    id_name = {"Road": "road_id", "Route": "route_id"}.get(object_type, "id")
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


def nearest_cell(point: tuple[float, float],
                 cells: Any,
                 max_distance_m: float) -> dict[str, Any] | None:
    best = None
    best_distance = max_distance_m
    for cell in candidate_cells(point, cells, max_distance_m):
        distance = distance_m(point, (float(cell["centroid_lon"]), float(cell["centroid_lat"])))
        if distance <= best_distance:
            best = cell
            best_distance = distance
    if not best:
        return None
    return {**best, "_distance_m": best_distance}


def build_cell_spatial_index(cells: list[dict[str, Any]]) -> dict[str, Any]:
    degree_size = 0.002
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for cell in cells:
        key = cell_bucket(
            (float(cell["centroid_lon"]), float(cell["centroid_lat"])),
            degree_size,
        )
        buckets.setdefault(key, []).append(cell)
    return {
        "degree_size": degree_size,
        "buckets": buckets,
        "cells": cells,
    }


def candidate_cells(point: tuple[float, float],
                    index: Any,
                    max_distance_m: float) -> list[dict[str, Any]]:
    if not isinstance(index, dict) or "buckets" not in index:
        return index
    degree_size = float(index["degree_size"])
    center = cell_bucket(point, degree_size)
    span = max(1, math.ceil(max_distance_m / (degree_size * 90_000.0)) + 1)
    candidates: list[dict[str, Any]] = []
    buckets = index["buckets"]
    for x in range(center[0] - span, center[0] + span + 1):
        for y in range(center[1] - span, center[1] + span + 1):
            candidates.extend(buckets.get((x, y), []))
    return candidates


def cell_bucket(point: tuple[float, float], degree_size: float) -> tuple[int, int]:
    lon, lat = point
    return math.floor(lon / degree_size), math.floor(lat / degree_size)


def row_point(row: dict[str, Any]) -> tuple[float, float] | None:
    lon = row.get("longitude")
    lat = row.get("latitude")
    if lon and lat:
        return float(lon), float(lat)
    geometry = json.loads(row.get("geometry") or "{}")
    centroid = geometry_centroid(geometry)
    return centroid


def sampled_geometry_points(row: dict[str, Any], max_points: int = 16) -> list[tuple[float, float]]:
    geometry = json.loads(row.get("geometry") or "{}")
    coords = iter_coords(geometry.get("coordinates") or [])
    if len(coords) <= max_points:
        return coords
    step = max(1, len(coords) // max_points)
    return coords[::step][:max_points]


def level_name(level: str) -> str:
    return {
        "red": "红色",
        "orange": "橙色",
        "yellow": "黄色",
        "blue": "蓝色",
    }.get(level, level)


class RiverModel:
    def __init__(self, points: list[tuple[float, float]], cumulative: list[float]):
        self.points = points
        self.cumulative = cumulative
        self.total_length = cumulative[-1] if cumulative else 0.0

    @classmethod
    def from_resolver(cls, resolver) -> "RiverModel":
        rows = resolver.query("River", limit=1)
        points = []
        if rows:
            geometry = json.loads(rows[0].get("geometry") or "{}")
            points = [(lon, lat) for lon, lat in iter_coords(geometry.get("coordinates") or [])]
        if len(points) > 300:
            stride = max(1, len(points) // 300)
            points = points[::stride] + points[-1:]
        cumulative = [0.0]
        for prev, curr in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + distance_m(prev, curr))
        return cls(points, cumulative)

    def distance_and_along(self, point: tuple[float, float]) -> tuple[float, float]:
        if len(self.points) < 2:
            return 0.0, 0.0
        best_distance = float("inf")
        best_along_m = 0.0
        for index, (start, end) in enumerate(zip(self.points, self.points[1:])):
            segment_distance, ratio = point_segment_distance_m(point, start, end)
            if segment_distance < best_distance:
                best_distance = segment_distance
                segment_length = self.cumulative[index + 1] - self.cumulative[index]
                best_along_m = self.cumulative[index] + segment_length * ratio
        along_ratio = best_along_m / self.total_length if self.total_length else 0.0
        return best_distance, max(0.0, min(1.0, along_ratio))


def geometry_centroid(geometry: dict) -> tuple[float, float] | None:
    coords = list(iter_coords(geometry.get("coordinates") or []))
    if not coords:
        return None
    return (
        sum(lon for lon, _ in coords) / len(coords),
        sum(lat for _, lat in coords) / len(coords),
    )


def iter_coords(value) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        return [(float(value[0]), float(value[1]))]
    coords: list[tuple[float, float]] = []
    for item in value:
        coords.extend(iter_coords(item))
    return coords


def point_segment_distance_m(point: tuple[float, float],
                             start: tuple[float, float],
                             end: tuple[float, float]) -> tuple[float, float]:
    px, py = project(point, point[1])
    ax, ay = project(start, point[1])
    bx, by = project(end, point[1])
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), 0.0
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    closest_x = ax + ratio * dx
    closest_y = ay + ratio * dy
    return math.hypot(px - closest_x, py - closest_y), ratio


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = project(a, (a[1] + b[1]) / 2)
    bx, by = project(b, (a[1] + b[1]) / 2)
    return math.hypot(ax - bx, ay - by)


def project(point: tuple[float, float], ref_lat: float) -> tuple[float, float]:
    lon, lat = point
    return (
        lon * 111_320.0 * math.cos(math.radians(ref_lat)),
        lat * 110_540.0,
    )


def normalize_forecast_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(filters or {})
    if result.get("forecast_id") == "latest":
        result["forecast_id"] = LATEST_FORECAST_ID
    return result


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    path.write_text(f"{body}\n" if body else "", encoding="utf-8")


def read_cached_emergency_cycle(forecast: dict[str, Any]) -> dict[str, Any] | None:
    if not FORECAST_CYCLE_PATH.exists():
        return None
    try:
        cached = json.loads(FORECAST_CYCLE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    cached_forecast = cached.get("forecast") or {}
    if (
        cached_forecast.get("forecast_id") == forecast.get("forecast_id")
        and cached_forecast.get("generated_at") == forecast.get("generated_at")
    ):
        cached.pop("mappable", None)
        return cached
    return None


def write_cached_emergency_cycle(result: dict[str, Any]) -> None:
    FORECAST_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_CYCLE_PATH.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def clear_cached_cycle() -> None:
    if FORECAST_CYCLE_PATH.exists():
        FORECAST_CYCLE_PATH.unlink()


def clear_cached_geojson() -> None:
    if not GENERATED_DIR.exists():
        return
    for path in GENERATED_DIR.glob("forecastcell*.geojson"):
        path.unlink()
