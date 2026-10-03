"""洪水约束下的路线候选筛选与几何校验。"""

from __future__ import annotations

import math
from typing import Any

from .amap_client import RoutingEngineError


DEFAULT_MAX_FLOOD_AREAS = 256


def empty_flood_areas(blocked_depth_m: float) -> dict[str, Any]:
    return {"feature_collection": {"type": "FeatureCollection", "features": []}, "summary": {
        "enabled": False, "blocked_depth_m": blocked_depth_m, "source_cell_count": 0,
        "area_count": 0, "aggregation_grid_m": 0,
    }}


def build_flood_avoidance_areas(cells: list[dict[str, Any]], blocked_depth_m: float,
                                max_areas: int = DEFAULT_MAX_FLOOD_AREAS,
                                initial_grid_m: float = 120.0) -> dict[str, Any]:
    wet_points = [
        (float(row["centroid_lon"]), float(row["centroid_lat"]))
        for row in cells
        if float(row.get("depth_m") or 0) >= blocked_depth_m
        and row.get("centroid_lon") is not None and row.get("centroid_lat") is not None
    ]
    if not wet_points:
        return empty_flood_areas(blocked_depth_m)
    ref_lat = sum(point[1] for point in wet_points) / len(wet_points)
    grid_m = max(30.0, float(initial_grid_m))
    rectangles: list[tuple[float, float, float, float]] = []
    for _ in range(8):
        rectangles = aggregate_wet_points(wet_points, ref_lat, grid_m)
        if len(rectangles) <= max_areas:
            break
        grid_m *= 1.5
    features = [{
        "type": "Feature", "id": f"flood_{index:03d}",
        "properties": {"blocked_depth_m": blocked_depth_m},
        "geometry": {"type": "Polygon", "coordinates": [[
            [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat],
            [min_lon, max_lat], [min_lon, min_lat],
        ]]},
    } for index, (min_lon, min_lat, max_lon, max_lat) in enumerate(rectangles[:max_areas])]
    return {"feature_collection": {"type": "FeatureCollection", "features": features}, "summary": {
        "enabled": bool(features), "blocked_depth_m": blocked_depth_m,
        "source_cell_count": len(wet_points), "area_count": len(features),
        "aggregation_grid_m": round(grid_m, 1),
    }}


def aggregate_wet_points(points: list[tuple[float, float]], ref_lat: float,
                         grid_m: float) -> list[tuple[float, float, float, float]]:
    lon_step = grid_m / max(1.0, 111_320.0 * math.cos(math.radians(ref_lat)))
    lat_step = grid_m / 110_540.0
    origin_lon, origin_lat = min(point[0] for point in points), min(point[1] for point in points)
    occupied = {(int(math.floor((lon - origin_lon) / lon_step)), int(math.floor((lat - origin_lat) / lat_step))) for lon, lat in points}
    by_row: dict[int, list[int]] = {}
    for x_index, y_index in occupied:
        by_row.setdefault(y_index, []).append(x_index)
    rectangles = []
    for y_index, x_values in sorted(by_row.items()):
        start = previous = min(x_values)
        for x_index in sorted(set(x_values))[1:]:
            if x_index == previous + 1:
                previous = x_index
                continue
            rectangles.append(grid_rectangle(origin_lon, origin_lat, lon_step, lat_step, start, previous, y_index))
            start = previous = x_index
        rectangles.append(grid_rectangle(origin_lon, origin_lat, lon_step, lat_step, start, previous, y_index))
    return rectangles


def grid_rectangle(origin_lon: float, origin_lat: float, lon_step: float,
                   lat_step: float, start_x: int, end_x: int,
                   y_index: int) -> tuple[float, float, float, float]:
    return (round(origin_lon + start_x * lon_step, 7), round(origin_lat + y_index * lat_step, 7),
            round(origin_lon + (end_x + 1) * lon_step, 7), round(origin_lat + (y_index + 1) * lat_step, 7))


def select_amap_route(candidates: list[dict[str, Any]], start: tuple[float, float],
                      destination: tuple[float, float], flood_areas: dict[str, Any],
                      flood_avoidance_enabled: bool, max_endpoint_distance_m: float,
                      max_detour_ratio: float) -> tuple[dict[str, Any], dict[str, float], dict[str, Any]]:
    diagnostics: dict[str, Any] = {"candidate_count": len(candidates), "safe_candidate_count": 0,
                                   "selected_candidate_index": None, "rejected_candidates": []}
    accepted = []
    for path in candidates:
        candidate_index = int(path.get("candidate_index") or 0)
        summary = {"candidate_index": candidate_index, "distance_m": round(float(path.get("distance") or 0), 1),
                   "duration_s": round(float(path.get("time") or 0) / 1000.0, 1)}
        coordinates = (path.get("points") or {}).get("coordinates") or []
        if flood_avoidance_enabled and path_intersects_areas(coordinates, flood_areas):
            diagnostics["rejected_candidates"].append({**summary, "reason": "intersects_flood"})
            continue
        try:
            evidence = validate_route_path(path, start, destination, max_endpoint_distance_m, max_detour_ratio)
        except RoutingEngineError as exc:
            diagnostics["rejected_candidates"].append({**summary, "reason": exc.status, "detail": str(exc)})
            continue
        accepted.append((path, evidence))
    diagnostics["safe_candidate_count"] = len(accepted)
    if not accepted:
        if any(item.get("reason") == "intersects_flood" for item in diagnostics["rejected_candidates"]):
            raise RoutingEngineError(f"高德返回的 {len(candidates)} 条候选路线均未满足当前预测淹没约束。", status="no_safe_route", details=diagnostics)
        raise RoutingEngineError("高德返回的候选路线均未通过路线有效性校验。", status="invalid_route", details=diagnostics)
    path, evidence = min(accepted, key=lambda item: (float(item[0].get("distance") or math.inf), float(item[0].get("time") or math.inf), int(item[0].get("candidate_index") or 0)))
    diagnostics["selected_candidate_index"] = int(path.get("candidate_index") or 0)
    return path, evidence, diagnostics


def path_intersects_areas(coordinates: list[list[float]], feature_collection: dict[str, Any]) -> bool:
    if len(coordinates) < 2:
        return False
    for first, second in zip(coordinates, coordinates[1:]):
        start, end = (float(first[0]), float(first[1])), (float(second[0]), float(second[1]))
        sample_count = max(1, math.ceil(distance_m(start, end) / 20.0))
        for index in range(sample_count + 1):
            ratio = index / sample_count
            point = (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
            if point_in_areas(point, feature_collection):
                return True
    return False


def point_in_areas(point: tuple[float, float], feature_collection: dict[str, Any]) -> bool:
    lon, lat = point
    for feature in feature_collection.get("features") or []:
        ring = ((feature.get("geometry") or {}).get("coordinates") or [[]])[0]
        if ring and min(float(item[0]) for item in ring) <= lon <= max(float(item[0]) for item in ring) and min(float(item[1]) for item in ring) <= lat <= max(float(item[1]) for item in ring):
            return True
    return False


def validate_route_path(path: dict[str, Any], start: tuple[float, float], destination: tuple[float, float],
                        max_endpoint_distance_m: float, max_detour_ratio: float) -> dict[str, float]:
    endpoints = ((path.get("matched_endpoints") or {}).get("coordinates") or [])
    if len(endpoints) < 2:
        raise RoutingEngineError("高德路线缺少起终点道路匹配信息。", status="invalid_route")
    start_distance = distance_m(start, (float(endpoints[0][0]), float(endpoints[0][1])))
    destination_distance = distance_m(destination, (float(endpoints[-1][0]), float(endpoints[-1][1])))
    if start_distance > max_endpoint_distance_m or destination_distance > max_endpoint_distance_m:
        raise RoutingEngineError("高德路线起点或终点距离领域对象坐标过远。", status="invalid_route")
    direct_distance = distance_m(start, destination)
    route_distance = float(path.get("distance") or 0)
    if route_distance < 10 and direct_distance > 50:
        raise RoutingEngineError("高德未形成有效路线。", status="invalid_route")
    detour_ratio = route_distance / max(direct_distance, 1.0)
    if direct_distance >= 100 and detour_ratio > max_detour_ratio:
        raise RoutingEngineError(f"高德路线绕行倍率过高：{detour_ratio:.1f}，允许值 {max_detour_ratio:.1f}。", status="invalid_route")
    return {"start_endpoint_distance_m": round(start_distance, 1), "destination_endpoint_distance_m": round(destination_distance, 1), "max_endpoint_distance_m": round(max_endpoint_distance_m, 1), "detour_ratio": round(detour_ratio, 2)}


def distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    ref_lat = (first[1] + second[1]) / 2
    return math.hypot((first[0] - second[0]) * 111_320.0 * math.cos(math.radians(ref_lat)), (first[1] - second[1]) * 110_540.0)


__all__ = [name for name in globals() if not name.startswith("_")]
