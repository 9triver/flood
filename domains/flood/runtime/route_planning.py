from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import rel
from .amap_client import (
    DEFAULT_AMAP_URL,
    RoutingEngineError,
    amap_request,
    call_amap,
    format_coordinate,
)
from .config import runtime_setting
from .coordinates import gcj02_to_wgs84
from .forecast_constants import LATEST_FORECAST_ID
from .forecast_query import query_forecast_cells
from .forecast_geometry import row_point
from .hydrodynamic_grid import forecast_time_context
from .forecast_context import resolve_routing_context, unavailable_forecast
from .route_safety import (
    build_flood_avoidance_areas,
    empty_flood_areas,
    point_in_areas,
    select_amap_route,
)
from .route_store import (
    planned_routes_path,
    save_planned_route,
)


DEFAULT_BLOCKED_DEPTH_M = 0.30
DEFAULT_FOOT_BLOCKED_DEPTH_M = 0.15
DEFAULT_MAX_ENDPOINT_DISTANCE_M = 800.0
DEFAULT_MAX_DETOUR_RATIO = 10.0
def plan_route(
    resolver,
    start_object_type: str = "EvacuationUnit",
    start_object_id: str = "",
    destination_site_id: str = "",
    start_lon: float | str | None = None,
    start_lat: float | str | None = None,
    destination_lon: float | str | None = None,
    destination_lat: float | str | None = None,
    forecast_id: str = "latest",
    time_h: float | str | None = None,
    blocked_depth_m: float | str | None = None,
    profile: str = "car",
    max_endpoint_distance_m: float = DEFAULT_MAX_ENDPOINT_DISTANCE_M,
    max_detour_ratio: float = DEFAULT_MAX_DETOUR_RATIO,
    view: str = "current",
) -> dict[str, Any]:
    _, start, start_name = resolve_start(
        resolver, start_object_type, start_object_id, start_lon, start_lat,
    )
    resolved_destination_site_id = (
        str(destination_site_id or "")
        or default_destination_site_id(
            resolver, start_object_type, start_object_id,
        )
    )
    destination_row, destination, destination_name = resolve_destination(
        resolver,
        resolved_destination_site_id,
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
    context = resolve_routing_context(forecast_id, time_h, view)
    if not context["available"]:
        return unavailable_forecast(context)
    forecast_verified = context["constraint_source"] == "forecast"
    analysis_time_h = context["time_h"]
    forecast_key = (LATEST_FORECAST_ID if forecast_id in ("", "latest") else forecast_id) if forecast_verified else ""
    time_fields = forecast_analysis_fields(forecast_key, analysis_time_h) if forecast_verified else {}
    flood_areas = empty_flood_areas(threshold)
    if forecast_verified:
        filters: dict[str, Any] = {"forecast_id": forecast_key}
        if analysis_time_h is not None:
            filters["time_h"] = analysis_time_h
        cells = query_forecast_cells(filters)
        flood_areas = build_flood_avoidance_areas(cells, threshold)
        flood_areas["summary"].update({
            "start_in_blocked_area": point_in_areas(start, flood_areas["feature_collection"]),
            "destination_in_blocked_area": point_in_areas(destination, flood_areas["feature_collection"]),
        })

    flood_areas["summary"].update({"requested": True, "forecast_verified": forecast_verified,
                                   "validation": "checked" if forecast_verified else "initial_dry",
                                   "constraint_source": context["constraint_source"], "basis": context.get("basis", "")})
    amap_key = routing_setting("AMAP_WEB_SERVICE_KEY", "")
    if not amap_key:
        return {
            "status": "routing_engine_unavailable",
            "error": "未配置 AMAP_WEB_SERVICE_KEY。",
            "routing_engine": "AMap",
            "start": endpoint_summary(start, start_object_type, start_object_id, start_name),
            "destination": endpoint_summary(destination, "EvacuationSite", resolved_destination_site_id, destination_name),
            "flood_avoidance": flood_areas["summary"],
            **time_fields,
        }
    timeout_seconds = float(routing_setting("AMAP_TIMEOUT_SECONDS", "20"))
    try:
        request_payload = amap_request(start, destination, routing_profile)
        response = call_amap(amap_key, request_payload, timeout_seconds)
        current_context = resolve_routing_context(forecast_id, time_h, view)
        if not current_context["available"]:
            return unavailable_forecast(current_context)
        signature_fields = ("workspace_id", "constraint_source", "forecast_input_id", "forecast_version", "time_h")
        if any(current_context.get(key) != context.get(key) for key in signature_fields):
            return {"status": "flood_state_changed", "error": "规划期间洪水状态已更新，请按当前状态重新规划。", "retryable": True}
        candidates = amap_route_paths(response, start, destination, routing_profile)
        path, route_evidence, routing_diagnostics = select_amap_route(
            candidates,
            start,
            destination,
            flood_areas["feature_collection"],
            True,
            max_endpoint_distance_m=max(
                0.0, float(max_endpoint_distance_m or DEFAULT_MAX_ENDPOINT_DISTANCE_M),
            ),
            max_detour_ratio=max(1.0, float(max_detour_ratio or DEFAULT_MAX_DETOUR_RATIO)),
        )
    except RoutingEngineError as exc:
        result = {
            "status": exc.status,
            "error": str(exc),
            "routing_engine": "AMap",
            "start": endpoint_summary(start, start_object_type, start_object_id, start_name),
            "destination": endpoint_summary(destination, "EvacuationSite", resolved_destination_site_id, destination_name),
            "flood_avoidance": flood_areas["summary"],
            **time_fields,
        }
        if exc.status in {"no_safe_route", "no_route", "invalid_route"}:
            result["retryable"] = False
        if exc.details:
            result["routing_diagnostics"] = exc.details
        return result

    route = make_route_record(
        path=path,
        start=start,
        destination=destination,
        start_object_type=start_object_type,
        start_object_id=start_object_id,
        start_name=start_name,
        destination_site_id=str(
            (destination_row or {}).get("evacuation_site_id")
            or resolved_destination_site_id
        ),
        destination_name=destination_name,
        forecast_id=forecast_key,
        time_h=analysis_time_h,
        blocked_depth_m=threshold,
        profile=routing_profile,
        flood_summary=flood_areas["summary"],
        request_payload=sanitize_amap_request(request_payload),
        route_evidence=route_evidence,
        routing_diagnostics=routing_diagnostics,
    )
    save_planned_route(route)
    return {
        "status": "completed",
        "route": route,
        "map_display": {
            "object_type": "EvacuationRoute",
            "filters": {
                "evacuation_route_id": route["evacuation_route_id"],
            },
            "fit": True,
        },
        "flood_context": context,
        "flood_avoidance": flood_areas["summary"],
        "routing_diagnostics": routing_diagnostics,
        **time_fields,
    }


def resolve_start(resolver, object_type: str, object_id: str,
                  lon: Any, lat: Any) -> tuple[dict[str, Any] | None, tuple[float, float] | None, str]:
    direct = coerce_point(lon, lat)
    if direct:
        return None, direct, "指定起点"
    if not object_id:
        return None, None, ""
    row = resolver.query_by_id(object_type or "EvacuationUnit", object_id)
    return row, safe_row_point(row), object_name(row, object_id)


def default_destination_site_id(
    resolver,
    start_object_type: str,
    start_object_id: str,
) -> str:
    if start_object_type != "EvacuationUnit" or not start_object_id:
        return ""
    routes = resolver.query(
        "EvacuationRoute",
        {"origin_unit_id": start_object_id},
    )
    routes.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    return next((
        str(row.get("destination_site_id") or "")
        for row in routes
        if row.get("destination_site_id")
    ), "")


def resolve_destination(resolver, site_id: str, lon: Any,
                        lat: Any) -> tuple[dict[str, Any] | None, tuple[float, float] | None, str]:
    direct = coerce_point(lon, lat)
    if direct:
        return None, direct, "指定终点"
    if not site_id:
        return None, None, ""
    row = resolver.query_by_id("EvacuationSite", site_id)
    return row, safe_row_point(row), object_name(row, site_id)


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










def amap_route_paths(response: dict[str, Any], start: tuple[float, float],
                     destination: tuple[float, float], profile: str) -> list[dict[str, Any]]:
    paths = ((response.get("route") or {}).get("paths") or [])
    if not paths:
        raise RoutingEngineError("高德未找到可通行路线。", status="no_route")
    candidates = []
    for candidate_index, route in enumerate(paths, start=1):
        coordinates: list[list[float]] = []
        instructions = []
        for step in route.get("steps") or []:
            step_coordinates = parse_amap_polyline(str(step.get("polyline") or ""))
            for coordinate in step_coordinates:
                if not coordinates or coordinate != coordinates[-1]:
                    coordinates.append(coordinate)
            step_cost = step.get("cost") or {}
            instructions.append({
                "text": str(step.get("instruction") or ""),
                "street_name": str(step.get("road_name") or step.get("road") or ""),
                "distance": coerce_number(
                    step.get("step_distance") if step.get("step_distance") is not None
                    else step.get("distance")
                ),
                "time": coerce_number(
                    step_cost.get("duration") if step_cost.get("duration") is not None
                    else step.get("duration")
                ) * 1000,
            })
        if len(coordinates) < 2:
            continue
        route_cost = route.get("cost") or {}
        candidates.append({
            "candidate_index": candidate_index,
            "distance": coerce_number(route.get("distance")),
            "time": coerce_number(
                route_cost.get("duration") if route_cost.get("duration") is not None
                else route.get("duration")
            ) * 1000,
            "points": {"type": "LineString", "coordinates": coordinates},
            "instructions": instructions,
            "matched_endpoints": {
                "type": "LineString",
                "coordinates": [coordinates[0], coordinates[-1]],
            },
            "profile": profile,
        })
    if not candidates:
        raise RoutingEngineError("高德路线响应缺少有效几何。", status="invalid_route")
    return candidates




def parse_amap_polyline(polyline: str) -> list[list[float]]:
    coordinates = []
    for item in polyline.split(";"):
        if not item or "," not in item:
            continue
        try:
            lng_text, lat_text = item.split(",", 1)
            lng, lat = gcj02_to_wgs84(float(lng_text), float(lat_text))
        except ValueError:
            continue
        coordinates.append([round(lng, 7), round(lat, 7)])
    return coordinates


def coerce_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def sanitize_amap_request(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "endpoint": payload.get("endpoint"),
        "profile": payload.get("profile"),
        "coordinate_crs": "GCJ-02",
        "params": dict(payload.get("params") or {}),
    }










def make_route_record(*, path: dict[str, Any], start: tuple[float, float],
                      destination: tuple[float, float], start_object_type: str,
                      start_object_id: str, start_name: str,
                      destination_site_id: str, destination_name: str,
                      forecast_id: str, time_h: float | None,
                      blocked_depth_m: float, profile: str,
                      flood_summary: dict[str, Any],
                      request_payload: dict[str, Any],
                      route_evidence: dict[str, float],
                      routing_diagnostics: dict[str, Any]) -> dict[str, Any]:
    coordinates = (path.get("points") or {}).get("coordinates") or []
    signature = json.dumps({
        "start": start,
        "destination": destination,
        "flood_validation": flood_summary["validation"],
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
        "evacuation_route_id": route_id,
        "name": f"{start_name or '起点'} 至 {destination_name or '终点'}避洪路线",
        "source_name": "",
        "name_source": "generated_by_routing_engine",
        "route_type": "transfer",
        "flood_validation": flood_summary["validation"],
        "flood_constraint_source": flood_summary["constraint_source"],
        "status": "planned",
        "road_detail": " -> ".join(road_names[:12]),
        "origin_unit_id": (
            start_object_id if start_object_type == "EvacuationUnit" else ""
        ),
        "destination_site_id": destination_site_id,
        "start_object_type": start_object_type,
        "start_object_id": start_object_id,
        "start_lon": start[0],
        "start_lat": start[1],
        "destination_lon": destination[0],
        "destination_lat": destination[1],
        "length_m": round(float(path.get("distance") or 0), 1),
        "duration_s": round(float(path.get("time") or 0) / 1000.0, 1),
        "profile": profile,
        "routing_engine": "AMap",
        "candidate_count": int(routing_diagnostics.get("candidate_count") or 0),
        "selected_candidate_index": int(
            routing_diagnostics.get("selected_candidate_index") or 0
        ),
        "rejected_candidate_count": len(
            routing_diagnostics.get("rejected_candidates") or []
        ),
        "forecast_id": forecast_id,
        "time_h": time_h,
        **(forecast_analysis_fields(forecast_id, time_h) if flood_summary["forecast_verified"] else {}),
        "blocked_depth_m": blocked_depth_m,
        "flood_area_count": int(flood_summary.get("area_count") or 0),
        "flood_source_cell_count": int(flood_summary.get("source_cell_count") or 0),
        **route_evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "geometry_type": "LineString",
        "geometry_crs": "EPSG:4326",
        "geometry": json.dumps({"type": "LineString", "coordinates": coordinates}, ensure_ascii=False),
        "instructions": json.dumps(instructions, ensure_ascii=False),
        "routing_request": json.dumps(request_payload, ensure_ascii=False),
        "data_path": rel(planned_routes_path(create=True)),
    }


def forecast_analysis_fields(forecast_id: str,
                             time_h: float | None) -> dict[str, Any]:
    context = forecast_time_context(forecast_id, time_h)
    return {
        "forecast_time": context.get("forecast_time"),
        "valid_from": context.get("valid_from"),
        "valid_to": context.get("valid_to"),
        "analysis_time_at": context.get("valid_at"),
    }












def routing_setting(name: str, default: str) -> str:
    return runtime_setting(name, default)


def endpoint_summary(point: tuple[float, float], object_type: str,
                     object_id: str, name: str) -> dict[str, Any]:
    return {
        "object_type": object_type,
        "object_id": object_id,
        "name": name,
        "longitude": point[0],
        "latitude": point[1],
    }
