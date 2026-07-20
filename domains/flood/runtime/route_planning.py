from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .common import GENERATED_DIR, PROJECT_DIR, rel
from .forecast import LATEST_FORECAST_ID, query_forecast_cells, row_point


PLANNED_ROUTES_PATH = GENERATED_DIR / "routing" / "planned_routes.jsonl"
DEFAULT_GRAPHHOPPER_URL = "http://127.0.0.1:8989"
DEFAULT_BLOCKED_DEPTH_M = 0.30
DEFAULT_FOOT_BLOCKED_DEPTH_M = 0.15
DEFAULT_MAX_FLOOD_AREAS = 256
DEFAULT_MAX_SNAP_DISTANCE_M = 800.0
DEFAULT_MAX_DETOUR_RATIO = 10.0
_ROUTE_WRITE_LOCK = threading.Lock()


class RoutingEngineError(RuntimeError):
    def __init__(self, message: str, status: str = "routing_engine_unavailable"):
        super().__init__(message)
        self.status = status


def plan_evacuation_route(
    resolver,
    start_object_type: str = "Transfer",
    start_object_id: str = "",
    destination_place_id: str = "",
    start_lon: float | str | None = None,
    start_lat: float | str | None = None,
    destination_lon: float | str | None = None,
    destination_lat: float | str | None = None,
    forecast_id: str = "latest",
    time_h: float | str | None = None,
    blocked_depth_m: float | str | None = None,
    profile: str = "car",
    avoid_flood: bool = True,
    max_snap_distance_m: float = DEFAULT_MAX_SNAP_DISTANCE_M,
    max_detour_ratio: float = DEFAULT_MAX_DETOUR_RATIO,
) -> dict[str, Any]:
    start_row, start, start_name = resolve_start(
        resolver, start_object_type, start_object_id, start_lon, start_lat,
    )
    destination_row, destination, destination_name = resolve_destination(
        resolver,
        destination_place_id or str((start_row or {}).get("place_id") or ""),
        destination_lon,
        destination_lat,
    )
    if not start:
        return {
            "status": "invalid_start",
            "error": "无法从起点对象或起点经纬度解析路线起点。",
        }
    if not destination:
        return {
            "status": "invalid_destination",
            "error": "无法从安置点对象或终点经纬度解析路线终点。",
        }

    routing_profile = str(profile or "car").lower()
    default_threshold = (
        DEFAULT_FOOT_BLOCKED_DEPTH_M
        if routing_profile == "foot"
        else DEFAULT_BLOCKED_DEPTH_M
    )
    threshold = max(0.0, float(
        default_threshold if blocked_depth_m in (None, "") else blocked_depth_m
    ))
    analysis_time_h = coerce_optional_float(time_h)
    forecast_key = LATEST_FORECAST_ID if forecast_id in ("", "latest") else forecast_id
    flood_areas = empty_flood_areas(threshold)
    if avoid_flood:
        filters: dict[str, Any] = {"forecast_id": forecast_key}
        if analysis_time_h is not None:
            filters["time_h"] = analysis_time_h
        cells = query_forecast_cells(resolver, filters)
        flood_areas = build_flood_avoidance_areas(cells, threshold)
        flood_areas["summary"].update({
            "start_in_blocked_area": point_in_areas(start, flood_areas["feature_collection"]),
            "destination_in_blocked_area": point_in_areas(destination, flood_areas["feature_collection"]),
        })

    request_payload = graphhopper_request(
        start,
        destination,
        profile=routing_profile,
        flood_areas=flood_areas,
    )
    graphhopper_url = routing_setting("GRAPHHOPPER_URL", DEFAULT_GRAPHHOPPER_URL)
    timeout_seconds = float(routing_setting("GRAPHHOPPER_TIMEOUT_SECONDS", "20"))
    try:
        response = call_graphhopper(graphhopper_url, request_payload, timeout_seconds)
        path = first_route_path(response)
        snap_evidence = validate_snapped_waypoints(
            path,
            start,
            destination,
            max_snap_distance_m=max(0.0, float(max_snap_distance_m or DEFAULT_MAX_SNAP_DISTANCE_M)),
            max_detour_ratio=max(1.0, float(max_detour_ratio or DEFAULT_MAX_DETOUR_RATIO)),
        )
    except RoutingEngineError as exc:
        return {
            "status": exc.status,
            "error": str(exc),
            "routing_engine": "GraphHopper",
            "routing_url": graphhopper_url,
            "start": endpoint_summary(start, start_object_type, start_object_id, start_name),
            "destination": endpoint_summary(destination, "Place", destination_place_id, destination_name),
            "flood_avoidance": flood_areas["summary"],
        }

    route = make_route_record(
        path=path,
        start=start,
        destination=destination,
        start_object_type=start_object_type,
        start_object_id=start_object_id,
        start_name=start_name,
        destination_place_id=str((destination_row or {}).get("place_id") or destination_place_id),
        destination_name=destination_name,
        forecast_id=forecast_key,
        time_h=analysis_time_h,
        blocked_depth_m=threshold,
        profile=routing_profile,
        flood_summary=flood_areas["summary"],
        request_payload=request_payload,
        snap_evidence=snap_evidence,
    )
    save_planned_route(route)
    return {
        "status": "completed",
        "route": route,
        "map_display": {
            "object_type": "Route",
            "filters": {"route_id": route["route_id"]},
            "fit": True,
        },
        "flood_avoidance": flood_areas["summary"],
    }


def resolve_start(resolver, object_type: str, object_id: str,
                  lon: Any, lat: Any) -> tuple[dict[str, Any] | None, tuple[float, float] | None, str]:
    direct = coerce_point(lon, lat)
    if direct:
        return None, direct, "指定起点"
    if not object_id:
        return None, None, ""
    row = resolver.query_by_id(object_type or "Transfer", object_id)
    return row, safe_row_point(row), object_name(row, object_id)


def resolve_destination(resolver, place_id: str, lon: Any,
                        lat: Any) -> tuple[dict[str, Any] | None, tuple[float, float] | None, str]:
    direct = coerce_point(lon, lat)
    if direct:
        return None, direct, "指定终点"
    if not place_id:
        return None, None, ""
    row = resolver.query_by_id("Place", place_id)
    return row, safe_row_point(row), object_name(row, place_id)


def safe_row_point(row: dict[str, Any] | None) -> tuple[float, float] | None:
    if not row:
        return None
    try:
        return row_point(row)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def coerce_point(lon: Any, lat: Any) -> tuple[float, float] | None:
    try:
        if lon in (None, "") or lat in (None, ""):
            return None
        point = (float(lon), float(lat))
    except (TypeError, ValueError):
        return None
    if not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
        return None
    return point


def object_name(row: dict[str, Any] | None, fallback: str) -> str:
    return str((row or {}).get("name") or fallback or "")


def coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def empty_flood_areas(blocked_depth_m: float) -> dict[str, Any]:
    return {
        "feature_collection": {"type": "FeatureCollection", "features": []},
        "summary": {
            "enabled": False,
            "blocked_depth_m": blocked_depth_m,
            "source_cell_count": 0,
            "area_count": 0,
            "aggregation_grid_m": 0,
        },
    }


def build_flood_avoidance_areas(cells: list[dict[str, Any]], blocked_depth_m: float,
                                max_areas: int = DEFAULT_MAX_FLOOD_AREAS,
                                initial_grid_m: float = 120.0) -> dict[str, Any]:
    wet_points = [
        (float(row["centroid_lon"]), float(row["centroid_lat"]))
        for row in cells
        if float(row.get("depth_m") or 0) >= blocked_depth_m
        and row.get("centroid_lon") is not None
        and row.get("centroid_lat") is not None
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

    features = []
    for index, (min_lon, min_lat, max_lon, max_lat) in enumerate(rectangles[:max_areas]):
        features.append({
            "type": "Feature",
            "id": f"flood_{index:03d}",
            "properties": {"blocked_depth_m": blocked_depth_m},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]],
            },
        })
    return {
        "feature_collection": {"type": "FeatureCollection", "features": features},
        "summary": {
            "enabled": bool(features),
            "blocked_depth_m": blocked_depth_m,
            "source_cell_count": len(wet_points),
            "area_count": len(features),
            "aggregation_grid_m": round(grid_m, 1),
        },
    }


def aggregate_wet_points(points: list[tuple[float, float]], ref_lat: float,
                         grid_m: float) -> list[tuple[float, float, float, float]]:
    lon_step = grid_m / max(1.0, 111_320.0 * math.cos(math.radians(ref_lat)))
    lat_step = grid_m / 110_540.0
    origin_lon = min(point[0] for point in points)
    origin_lat = min(point[1] for point in points)
    occupied = {
        (
            int(math.floor((lon - origin_lon) / lon_step)),
            int(math.floor((lat - origin_lat) / lat_step)),
        )
        for lon, lat in points
    }
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
            rectangles.append(grid_rectangle(
                origin_lon, origin_lat, lon_step, lat_step,
                start, previous, y_index,
            ))
            start = previous = x_index
        rectangles.append(grid_rectangle(
            origin_lon, origin_lat, lon_step, lat_step,
            start, previous, y_index,
        ))
    return rectangles


def grid_rectangle(origin_lon: float, origin_lat: float,
                   lon_step: float, lat_step: float,
                   start_x: int, end_x: int, y_index: int) -> tuple[float, float, float, float]:
    return (
        round(origin_lon + start_x * lon_step, 7),
        round(origin_lat + y_index * lat_step, 7),
        round(origin_lon + (end_x + 1) * lon_step, 7),
        round(origin_lat + (y_index + 1) * lat_step, 7),
    )


def graphhopper_request(start: tuple[float, float], destination: tuple[float, float],
                        profile: str, flood_areas: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "points": [[start[0], start[1]], [destination[0], destination[1]]],
        "profile": profile,
        "locale": "zh_CN",
        "points_encoded": False,
        "instructions": True,
    }
    features = flood_areas["feature_collection"]["features"]
    if features:
        payload["ch.disable"] = True
        payload["custom_model"] = {
            "priority": [
                {"if": f"in_{feature['id']}", "multiply_by": "0"}
                for feature in features
            ],
            "areas": flood_areas["feature_collection"],
        }
    return payload


def point_in_areas(point: tuple[float, float], feature_collection: dict[str, Any]) -> bool:
    lon, lat = point
    for feature in feature_collection.get("features") or []:
        ring = ((feature.get("geometry") or {}).get("coordinates") or [[]])[0]
        if not ring:
            continue
        lons = [float(item[0]) for item in ring]
        lats = [float(item[1]) for item in ring]
        if min(lons) <= lon <= max(lons) and min(lats) <= lat <= max(lats):
            return True
    return False


def call_graphhopper(base_url: str, payload: dict[str, Any],
                     timeout_seconds: float) -> dict[str, Any]:
    endpoint = f"{base_url.rstrip('/')}/route"
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        no_route_markers = ("PointNotFoundException", "ConnectionNotFoundException", "not found")
        if exc.code in {400, 404, 422} and any(marker in detail for marker in no_route_markers):
            status = "no_safe_route"
        elif exc.code in {400, 404, 422}:
            status = "routing_request_invalid"
        else:
            status = "routing_engine_unavailable"
        raise RoutingEngineError(
            f"GraphHopper 返回 HTTP {exc.code}: {detail[:800]}",
            status=status,
        ) from exc
    except URLError as exc:
        raise RoutingEngineError(f"无法连接 GraphHopper {endpoint}: {exc.reason}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingEngineError(f"GraphHopper 响应不可用: {exc}") from exc
    if not isinstance(result, dict):
        raise RoutingEngineError("GraphHopper 返回了无效响应。")
    if result.get("message") and not result.get("paths"):
        hints = result.get("hints") or []
        raise RoutingEngineError(
            f"GraphHopper 无法规划路线: {result['message']} {hints}",
            status="no_safe_route",
        )
    return result


def first_route_path(response: dict[str, Any]) -> dict[str, Any]:
    paths = response.get("paths") or []
    if not paths:
        raise RoutingEngineError(
            "GraphHopper 未找到满足当前洪水约束的可通行路线。",
            status="no_safe_route",
        )
    path = paths[0]
    coordinates = ((path.get("points") or {}).get("coordinates") or [])
    if len(coordinates) < 2:
        raise RoutingEngineError("GraphHopper 路线响应缺少有效几何。")
    return path


def validate_snapped_waypoints(path: dict[str, Any], start: tuple[float, float],
                               destination: tuple[float, float],
                               max_snap_distance_m: float,
                               max_detour_ratio: float) -> dict[str, float]:
    snapped = ((path.get("snapped_waypoints") or {}).get("coordinates") or [])
    if len(snapped) < 2:
        raise RoutingEngineError(
            "GraphHopper 路线缺少起终点道路吸附信息。",
            status="invalid_route",
        )
    start_snap = distance_m(start, (float(snapped[0][0]), float(snapped[0][1])))
    destination_snap = distance_m(
        destination,
        (float(snapped[-1][0]), float(snapped[-1][1])),
    )
    if start_snap > max_snap_distance_m or destination_snap > max_snap_distance_m:
        raise RoutingEngineError(
            "路线起点或终点距离可通行 OSM 道路过远："
            f"起点 {start_snap:.1f} m，终点 {destination_snap:.1f} m，"
            f"允许值 {max_snap_distance_m:.1f} m。",
            status="invalid_route",
        )
    direct_distance = distance_m(start, destination)
    route_distance = float(path.get("distance") or 0)
    if route_distance < 10 and direct_distance > 50:
        raise RoutingEngineError(
            "GraphHopper 将起终点吸附到同一道路节点，未形成有效路线。",
            status="invalid_route",
        )
    detour_ratio = route_distance / max(direct_distance, 1.0)
    if direct_distance >= 100 and detour_ratio > max_detour_ratio:
        raise RoutingEngineError(
            f"路线绕行倍率过高：{detour_ratio:.1f}，允许值 {max_detour_ratio:.1f}。"
            "当前 OSM 局部路网可能不完整。",
            status="invalid_route",
        )
    return {
        "start_snap_distance_m": round(start_snap, 1),
        "destination_snap_distance_m": round(destination_snap, 1),
        "max_snap_distance_m": round(max_snap_distance_m, 1),
        "detour_ratio": round(detour_ratio, 2),
    }


def distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    ref_lat = (first[1] + second[1]) / 2
    dx = (first[0] - second[0]) * 111_320.0 * math.cos(math.radians(ref_lat))
    dy = (first[1] - second[1]) * 110_540.0
    return math.hypot(dx, dy)


def make_route_record(*, path: dict[str, Any], start: tuple[float, float],
                      destination: tuple[float, float], start_object_type: str,
                      start_object_id: str, start_name: str,
                      destination_place_id: str, destination_name: str,
                      forecast_id: str, time_h: float | None,
                      blocked_depth_m: float, profile: str,
                      flood_summary: dict[str, Any],
                      request_payload: dict[str, Any],
                      snap_evidence: dict[str, float]) -> dict[str, Any]:
    coordinates = (path.get("points") or {}).get("coordinates") or []
    signature = json.dumps({
        "start": start,
        "destination": destination,
        "forecast_id": forecast_id,
        "time_h": time_h,
        "blocked_depth_m": blocked_depth_m,
        "profile": profile,
        "geometry": coordinates,
    }, sort_keys=True, ensure_ascii=False)
    route_id = f"planned_{hashlib.sha1(signature.encode('utf-8')).hexdigest()[:16]}"
    instructions = path.get("instructions") or []
    road_names = []
    for instruction in instructions:
        name = str(instruction.get("street_name") or instruction.get("text") or "").strip()
        if name and name not in road_names:
            road_names.append(name)
    return {
        "route_id": route_id,
        "name": f"{start_name or '起点'} 至 {destination_name or '终点'}避洪路线",
        "route_type": "transfer",
        "status": "planned",
        "road_detail": " -> ".join(road_names[:12]),
        "start_object_type": start_object_type,
        "start_object_id": start_object_id,
        "place_id": destination_place_id,
        "start_lon": start[0],
        "start_lat": start[1],
        "destination_lon": destination[0],
        "destination_lat": destination[1],
        "length_m": round(float(path.get("distance") or 0), 1),
        "duration_s": round(float(path.get("time") or 0) / 1000.0, 1),
        "profile": profile,
        "routing_engine": "GraphHopper",
        "forecast_id": forecast_id,
        "time_h": time_h,
        "blocked_depth_m": blocked_depth_m,
        "flood_area_count": int(flood_summary.get("area_count") or 0),
        "flood_source_cell_count": int(flood_summary.get("source_cell_count") or 0),
        **snap_evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "geometry_type": "LineString",
        "geometry_crs": "EPSG:4326",
        "geometry": json.dumps({"type": "LineString", "coordinates": coordinates}, ensure_ascii=False),
        "instructions": json.dumps(instructions, ensure_ascii=False),
        "routing_request": json.dumps(request_payload, ensure_ascii=False),
        "data_path": rel(PLANNED_ROUTES_PATH),
    }


def save_planned_route(route: dict[str, Any]) -> None:
    with _ROUTE_WRITE_LOCK:
        rows = read_planned_routes()
        rows = [row for row in rows if row.get("route_id") != route.get("route_id")]
        rows.append(route)
        rows = rows[-100:]
        PLANNED_ROUTES_PATH.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        temp_path = PLANNED_ROUTES_PATH.with_suffix(".jsonl.tmp")
        temp_path.write_text(f"{body}\n", encoding="utf-8")
        temp_path.replace(PLANNED_ROUTES_PATH)


def read_planned_routes() -> list[dict[str, Any]]:
    if not PLANNED_ROUTES_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in PLANNED_ROUTES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def routing_setting(name: str, default: str) -> str:
    if os.environ.get(name):
        return str(os.environ[name])
    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'") or default
    return default


def endpoint_summary(point: tuple[float, float], object_type: str,
                     object_id: str, name: str) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_id": object_id,
        "name": name,
        "longitude": point[0],
        "latitude": point[1],
    }
