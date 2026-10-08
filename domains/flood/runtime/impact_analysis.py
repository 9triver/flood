from __future__ import annotations

import json
import math
from typing import Any

from .common import id_field
from .forecast_constants import LATEST_FORECAST_ID
from .forecast_query import query_forecast_cells, risk_level
from .forecast_geometry import (
    build_cell_spatial_index,
    iter_coords,
    nearby_cells,
    nearest_cell,
    point_segment_distance_m,
    row_point,
)
from .hydrodynamic_grid import forecast_time_context
from .impact_scope import ImpactScope
from .road_routes import ROAD_ROUTE_SCOPE
from .linear_inundation import METRIC_CRS, WetCellIndex


POINT_TARGET_TYPES = ("Facility", "EvacuationUnit", "EvacuationSite", "Station")
LINE_TARGET_TYPES = ("Road", "EvacuationRoute")
TARGET_TYPES = (*POINT_TARGET_TYPES, "Bridge", *LINE_TARGET_TYPES)
BRIDGE_INFLUENCE_RADIUS_M = 80.0


def analyze_inundation_impacts(
    resolver,
    forecast_id: str = "latest",
    target_type: str = "all",
    min_depth_m: float = 0.15,
    max_distance_m: float = 10.0,
    time_h: float | None = None,
    bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
    object_ids: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    *,
    forecast_cells: list[dict[str, Any]] | None = None,
    time_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    forecast_key = LATEST_FORECAST_ID if forecast_id in ("", "latest") else forecast_id
    analysis_time_h = coerce_time_h(time_h)
    target_types = resolve_target_types(target_type)
    if not target_types:
        return {
            "status": "invalid_target_type",
            "forecast_id": forecast_key,
            "time_h": analysis_time_h,
            "target_type": target_type,
            "valid_target_types": ["all", *TARGET_TYPES, "RoadRoute"],
            "summary": {},
            "total_impacts": 0,
            "impacts": [],
            **(time_context if time_context is not None else analysis_time_fields(forecast_key, analysis_time_h)),
        }

    try:
        scope = ImpactScope(resolver, target_types, object_ids, filters)
    except (ValueError, TypeError) as exc:
        return {"status": "invalid_scope", "error": str(exc)}
    resolver = scope
    cell_filters: dict[str, Any] = {"forecast_id": forecast_key}
    if analysis_time_h is not None:
        cell_filters["time_h"] = analysis_time_h
    cells = query_forecast_cells(cell_filters) if forecast_cells is None else forecast_cells
    if not cells:
        time_fields = time_context if time_context is not None else analysis_time_fields(forecast_key, analysis_time_h)
        return {
            "status": "no_forecast_cells",
            "analysis_scope": scope.description,
            "forecast_id": forecast_key,
            "time_h": analysis_time_h,
            "target_type": target_type,
            "summary": {item: 0 for item in target_types},
            "total_impacts": 0,
            "impacts": [],
            "basis": analysis_basis(
                analysis_time_h,
                time_fields.get("analysis_time_at"),
                empty=True,
            ),
            **time_fields,
        }

    minimum_depth = float(min_depth_m or 0)
    # Index every qualifying cell. Subsampling wet cells can hide intersections.
    cell_index = build_cell_spatial_index([
        row for row in cells if float(row.get("depth_m") or 0) >= minimum_depth
        and row.get("centroid_lon") is not None and row.get("centroid_lat") is not None
    ])
    linear_index = WetCellIndex(cells, minimum_depth) if set(target_types) & {*LINE_TARGET_TYPES, "RoadRoute"} else None
    bridge_cell_index = None
    if "Bridge" in target_types:
        bridge_cell_index = build_cell_spatial_index([
            row for row in cells
            if float(row.get("depth_m") or 0) >= minimum_depth
            and row.get("centroid_lon") is not None
            and row.get("centroid_lat") is not None
        ])
    resolved_forecast_id = str(cells[0].get("forecast_id") or forecast_key)
    impacts: list[dict[str, Any]] = []
    nearby_impacts: list[dict[str, Any]] = []
    unassessed_objects: list[dict[str, str]] = []
    for object_type in target_types:
        if object_type == "RoadRoute":
            # Analyze each complete segment, then aggregate membership.
            object_type = "Road"
        if object_type == "Bridge":
            impacts.extend(analyze_bridge_objects(
                resolver,
                bridge_cell_index,
                min_depth_m=minimum_depth,
                influence_radius_m=float(bridge_influence_radius_m or 0),
            ))
        elif object_type in POINT_TARGET_TYPES:
            impacts.extend(analyze_point_objects(
                resolver,
                object_type,
                cell_index,
                min_depth_m=minimum_depth,
                max_distance_m=float(max_distance_m or 0),
            ))
        else:
            linear_impacts = analyze_linear_objects(
                resolver,
                object_type,
                linear_index,
                min_depth_m=minimum_depth,
                max_distance_m=float(max_distance_m or 0),
                unassessed_objects=unassessed_objects,
            )
            impacts.extend(row for row in linear_impacts if row["impact_status"] != "nearby_flood")
            nearby_impacts.extend(row for row in linear_impacts if row["impact_status"] == "nearby_flood")

    impacts = sorted(
        impacts,
        key=lambda row: (
            -risk_rank(str(row.get("risk_level") or "")),
            -float(row.get("depth_m") or 0),
            float(row.get("distance_m") or 0),
        ),
    )
    route_fields = {}
    if "RoadRoute" in target_types or ("Road" in target_types and not scope.explicit):
        road_impacts = [row for row in impacts if row["object_type"] == "Road"]
        nearby_roads = [row for row in nearby_impacts if row["object_type"] == "Road"]
        route_impacts, nearby_routes, coverage = aggregate_road_route_impacts(resolver, road_impacts, nearby_roads)
        route_fields = {
            "road_route_impacts": route_impacts,
            "road_route_nearby_impacts": nearby_routes,
            "road_route_coverage": coverage,
        }
        if target_types == ["RoadRoute"]:
            impacts = route_impacts
            nearby_impacts = nearby_routes
    summary = summarize_impacts(target_types, impacts)
    actual_time_h = actual_cell_time_h(cells, analysis_time_h)
    time_fields = time_context if time_context is not None else analysis_time_fields(resolved_forecast_id, actual_time_h)
    return {
        "status": "partial" if unassessed_objects or (linear_index and linear_index.skipped_cell_ids) else "completed",
        "forecast_id": resolved_forecast_id,
        "analysis_scope": scope.description,
        "time_h": actual_time_h,
        **time_fields,
        "target_type": target_type or "all",
        "parameters": {
            "min_depth_m": float(min_depth_m or 0),
            "max_distance_m": float(max_distance_m or 0),
            "bridge_influence_radius_m": float(bridge_influence_radius_m or 0),
            "time_h": analysis_time_h,
        },
        "summary": summary,
        "affected_object_ids": affected_object_ids(target_types, impacts),
        "total_impacts": len(impacts),
        "nearby_impacts": nearby_impacts,
        "nearby_object_ids": affected_object_ids(target_types, nearby_impacts),
        "nearby_summary": summarize_impacts(target_types, nearby_impacts),
        "total_nearby": len(nearby_impacts),
        "linear_analysis": {
            "method": "full_line_polygon_overlay",
            "metric_crs": METRIC_CRS,
            "indexed_wet_cell_count": len(linear_index.rows) if linear_index else 0,
            "skipped_cell_ids": linear_index.skipped_cell_ids if linear_index else [],
            "unassessed_objects": unassessed_objects,
            "nearby_distance_m": max(0.0, float(max_distance_m or 0)),
        },
        "basis": analysis_basis(actual_time_h, time_fields.get("analysis_time_at")),
        "impacts": impacts,
        **route_fields,
    }


def coerce_time_h(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def actual_cell_time_h(cells: list[dict[str, Any]], fallback: float | None) -> float | None:
    if fallback is None:
        return None
    for cell in cells:
        value = coerce_time_h(cell.get("time_h", cell.get("lead_time_h")))
        if value is not None:
            return round(value, 3)
    return round(float(fallback), 3)


def analysis_time_fields(forecast_id: str, time_h: float | None) -> dict[str, Any]:
    context = forecast_time_context(forecast_id, time_h)
    return {
        "forecast_time": context.get("forecast_time"),
        "valid_from": context.get("valid_from"),
        "valid_to": context.get("valid_to"),
        "analysis_time_at": context.get("valid_at"),
    }


def analysis_basis(time_h: float | None, analysis_time_at: str | None = None,
                   empty: bool = False) -> str:
    prefix = (
        (
            f"使用水动力模型 {analysis_time_at}（预测 +{time_h:.3f} h）的 "
            "InundationForecastCell 预测淹没网格"
        )
        if time_h is not None and analysis_time_at
        else f"使用水动力模型预测 +{time_h:.3f} h 时刻的 InundationForecastCell 预测淹没网格"
        if time_h is not None
        else "使用最新 InundationForecastCell 最大水深包络预测淹没网格"
    )
    if empty:
        return f"{prefix}执行叠加分析；未找到满足水深阈值的预测淹没单元。"
    return (
        f"{prefix}执行确定性空间邻近分析；"
        "普通点对象按对象坐标匹配最近淹没网格，桥梁按完整网格多边形执行桥头影响区分析，"
        "道路和转移路线使用完整线形与全部达标湿网格多边形求交，距离在本地投影下以米计算；"
        "仅邻近的对象另列 nearby_impacts，不计入受影响对象数；缺少几何时标记未评估，不能解释为安全。"
    )


def resolve_target_types(target_type: str) -> list[str]:
    value = str(target_type or "all").strip()
    if not value or value.lower() == "all":
        return list(TARGET_TYPES)
    aliases = {
        "facility": "Facility",
        "bridge": "Bridge",
        "transfer": "EvacuationUnit",
        "place": "EvacuationSite",
        "road": "Road",
        "roadroute": "RoadRoute",
        "route": "EvacuationRoute",
    }
    canonical = aliases.get(value.lower(), value)
    return [canonical] if canonical in (*TARGET_TYPES, "RoadRoute") else []


def aggregate_road_route_impacts(resolver, road_impacts: list[dict],
                                 nearby_roads: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Count intersecting segments separately from segments only near flooding."""
    routes = resolver.query("RoadRoute")
    by_id = {str(row["object_id"]): row for row in road_impacts}
    nearby_by_id = {str(row["object_id"]): row for row in nearby_roads}
    grouped_ids = {str(road_id) for route in routes for road_id in route["road_ids"]}
    results, nearby_results = [], []
    for route in routes:
        affected = [by_id[str(road_id)] for road_id in route["road_ids"] if str(road_id) in by_id]
        nearby = [nearby_by_id[str(road_id)] for road_id in route["road_ids"] if str(road_id) in nearby_by_id]
        members = affected or nearby
        if not members:
            continue
        deepest = max(members, key=lambda row: float(row.get("depth_m") or 0))
        result = {
            **{key: deepest[key] for key in (
                "depth_m", "velocity_mps", "distance_m", "forecast_cell_id", "mesh_cell_id", "longitude", "latitude",
            )},
            **{key: deepest.get(key) for key in ("depth_source", "velocity_source", "risk_basis")},
            "object_type": "RoadRoute",
            "object_id": route["road_route_id"],
            "name": route["name"],
            "ref": route["ref"],
            "risk_level": max(members, key=lambda row: risk_rank(row["risk_level"]))["risk_level"],
            "basis": "aggregated_road_segment_impacts",
            "impact_status": "forecast_overlap" if affected else "nearby_flood",
            "directly_inundated": False,
            "passability_status": "not_assessed" if affected else "inspection_required",
            "depth_basis": "intersecting_forecast_cells" if affected else "nearby_forecast_cells",
            "coverage_scope": ROAD_ROUTE_SCOPE,
            "recorded_segment_count": route["segment_count"],
            "geometry_segment_count": route["geometry_segment_count"],
            "affected_segment_count": len(affected),
            "nearby_segment_count": len(nearby),
            "structure_unverified_segment_count": sum(row.get("impact_status") == "structure_overlap_unverified" for row in affected),
            "affected_road_ids": [row["object_id"] for row in affected],
            "nearby_road_ids": [row["object_id"] for row in nearby],
            "segment_impacts": affected,
            "nearby_segment_impacts": nearby,
        }
        (results if affected else nearby_results).append(result)
    sort_key = lambda row: (-risk_rank(row["risk_level"]), -float(row["depth_m"]), row["object_id"])
    results.sort(key=sort_key)
    nearby_results.sort(key=sort_key)
    return results, nearby_results, {
        "coverage_scope": ROAD_ROUTE_SCOPE,
        "recorded_route_count": len(routes),
        "grouped_segment_count": len(grouped_ids),
        "ungrouped_segment_count": len(resolver.query("Road")) - len(grouped_ids),
        "affected_route_count": len(results),
        "nearby_only_route_count": len(nearby_results),
        "affected_segment_count": len(by_id),
        "nearby_segment_count": len(nearby_by_id),
        "ungrouped_affected_road_ids": sorted(set(by_id) - grouped_ids),
        "ungrouped_nearby_road_ids": sorted(set(nearby_by_id) - grouped_ids),
        "note": "受影响数仅计与预测湿网格相交的路段，邻近积水另计。共线路段可影响多条道路，道路数与路段数不可相加；受影响不等于整条道路不可通行。",
    }


def analyze_bridge_objects(
    resolver,
    cell_index: Any,
    min_depth_m: float,
    influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
) -> list[dict[str, Any]]:
    impacts = []
    river_points = river_geometry_points(resolver)
    for row in resolver.query("Bridge"):
        point = safe_row_point(row)
        if not point:
            continue
        matched = [
            cell for cell in nearby_cells(
                point,
                cell_index,
                max_distance_m=influence_radius_m,
            )
            if float(cell.get("depth_m") or 0) >= min_depth_m
        ]
        if not matched:
            continue

        deepest = max(matched, key=lambda cell: float(cell.get("depth_m") or 0))
        nearest_distance = min(
            float(cell.get("_distance_m") or 0) for cell in matched
        )
        affected_sides = affected_river_sides(point, matched, river_points)
        approaches_inundated = len(affected_sides) >= 2
        basis = (
            "bridge_approach_inundated"
            if approaches_inundated
            else "bridge_influence_zone"
        )
        impact = make_impact(
            "Bridge",
            row,
            "bridge_id",
            deepest,
            basis,
            point,
        )
        impact.update({
            "directly_inundated": False,
            "impact_status": basis,
            "passability_status": (
                "likely_impassable" if approaches_inundated else "inspection_required"
            ),
            "data_quality": "insufficient_bridge_elevation",
            "depth_basis": "nearby_floodplain_forecast",
            "distance_m": round(nearest_distance, 1),
            "max_depth_cell_distance_m": round(
                float(deepest.get("_distance_m") or 0),
                1,
            ),
            "nearby_max_depth_m": round(float(deepest.get("depth_m") or 0), 3),
            "nearby_max_velocity_mps": impact["velocity_mps"],
            "nearby_cell_count": len(matched),
            "bridge_influence_radius_m": round(float(influence_radius_m), 1),
            "affected_side_count": len(affected_sides),
            "affected_bank_sides": affected_sides,
        })
        impacts.append(impact)
    return impacts


def river_geometry_points(resolver) -> list[tuple[float, float]]:
    rows = resolver.query("River")[:1]
    if not rows:
        return []
    geometry = rows[0].get("geometry") or {}
    if isinstance(geometry, str):
        try:
            geometry = json.loads(geometry)
        except json.JSONDecodeError:
            return []
    if not isinstance(geometry, dict):
        return []
    return iter_coords(geometry.get("coordinates") or [])


def affected_river_sides(
    point: tuple[float, float],
    cells: list[dict[str, Any]],
    river_points: list[tuple[float, float]],
) -> list[str]:
    tangent = nearest_river_tangent(point, river_points)
    if not tangent:
        return []
    tx, ty = tangent
    cos_lat = math.cos(math.radians(point[1]))
    sides = set()
    for cell in cells:
        try:
            rx = (float(cell["centroid_lon"]) - point[0]) * cos_lat
            ry = float(cell["centroid_lat"]) - point[1]
        except (KeyError, TypeError, ValueError):
            continue
        cross = tx * ry - ty * rx
        if cross > 1e-12:
            sides.add("left")
        elif cross < -1e-12:
            sides.add("right")
    return [side for side in ("left", "right") if side in sides]


def nearest_river_tangent(
    point: tuple[float, float],
    river_points: list[tuple[float, float]],
) -> tuple[float, float] | None:
    if len(river_points) < 2:
        return None
    best_distance = float("inf")
    best_tangent = None
    cos_lat = math.cos(math.radians(point[1]))
    for start, end in zip(river_points, river_points[1:]):
        segment_distance, _ = point_segment_distance_m(point, start, end)
        if segment_distance >= best_distance:
            continue
        tx = (end[0] - start[0]) * cos_lat
        ty = end[1] - start[1]
        if tx == 0 and ty == 0:
            continue
        best_distance = segment_distance
        best_tangent = (tx, ty)
    return best_tangent


def analyze_point_objects(resolver, object_type: str, cell_index: Any,
                          min_depth_m: float,
                          max_distance_m: float) -> list[dict[str, Any]]:
    impacts = []
    object_id_field = id_field(object_type)
    for row in resolver.query(object_type):
        point = safe_row_point(row)
        if not point:
            continue
        cell = nearest_cell(point, cell_index, max_distance_m=max_distance_m)
        if not cell:
            continue
        depth = float(cell.get("depth_m") or 0)
        if depth < min_depth_m:
            continue
        impacts.append(make_impact(
            object_type,
            row,
            object_id_field,
            cell,
            "point_nearest_cell",
            point,
        ))
    return impacts


def analyze_linear_objects(resolver, object_type: str, cell_index: Any,
                           min_depth_m: float,
                           max_distance_m: float,
                           unassessed_objects: list[dict] | None = None) -> list[dict]:
    index = cell_index if isinstance(cell_index, WetCellIndex) else WetCellIndex(
        cell_index.get("cells", []) if isinstance(cell_index, dict) else cell_index,
        min_depth_m,
    )
    impacts = []
    object_id_field = id_field(object_type)
    for row in resolver.query(object_type):
        matched = index.match(row, max_distance_m)
        if matched is None:
            if unassessed_objects is not None:
                unassessed_objects.append({
                    "object_type": object_type, "object_id": str(row.get(object_id_field) or ""),
                    "reason": "missing_or_invalid_line_geometry",
                })
            continue
        if matched["status"] == "no_match":
            continue
        overlap = matched["status"] == "forecast_overlap"
        structure_unverified = overlap and object_type == "Road" and (
            row.get("bridge_flag") or row.get("tunnel_flag")
        )
        impact = make_impact(
            object_type, row, object_id_field, matched["cell"],
            "line_polygon_intersection" if overlap else "line_polygon_proximity",
            matched["point"],
        )
        impact.update({
            "impact_status": "structure_overlap_unverified" if structure_unverified else matched["status"],
            "directly_inundated": overlap and not structure_unverified,
            "passability_status": "inspection_required" if not overlap or structure_unverified else "not_assessed",
            "depth_basis": "intersecting_forecast_cells" if overlap else "nearby_forecast_cells",
            "overlap_length_m": matched["overlap_length_m"],
            "intersecting_mesh_cell_ids": matched["intersecting_mesh_cell_ids"],
            "nearby_mesh_cell_ids": matched["nearby_mesh_cell_ids"],
            "intersecting_cell_count": len(matched["intersecting_mesh_cell_ids"]),
            "nearby_cell_count": len(matched["nearby_mesh_cell_ids"]),
            "nearest_distance_m": matched["nearest_distance_m"],
        })
        if structure_unverified:
            impact["data_quality"] = "road_surface_elevation_unverified"
        impacts.append(impact)
    return impacts


def make_impact(object_type: str, row: dict[str, Any], object_id_field: str,
                cell: dict[str, Any], basis: str,
                impact_point: tuple[float, float]) -> dict[str, Any]:
    depth = float(cell.get("depth_m") or 0)
    velocity = float(cell["velocity_mps"]) if cell.get("velocity_mps") is not None else None
    impact = {
        "object_type": object_type,
        "object_id": str(row.get(object_id_field) or ""),
        "name": row.get("name") or row.get(object_id_field) or "",
        "risk_level": cell.get("risk_level") or risk_level(depth, velocity or 0),
        "depth_m": round(depth, 3),
        "depth_source": cell.get("depth_source", "unspecified"),
        "velocity_mps": round(velocity, 3) if velocity is not None else None,
        "velocity_source": cell.get("velocity_source", "unspecified" if velocity is not None else "unavailable"),
        "risk_basis": cell.get("risk_basis", "unspecified"),
        "distance_m": round(float(cell.get("_distance_m") or 0), 1),
        "forecast_cell_id": cell.get("forecast_cell_id", ""),
        "mesh_cell_id": cell.get("mesh_cell_id", ""),
        "longitude": round(float(impact_point[0]), 7),
        "latitude": round(float(impact_point[1]), 7),
        "basis": basis,
        "directly_inundated": True,
    }
    if object_type == "Facility":
        impact["facility_type"] = str(row.get("facility_type") or "")
        impact["subtype"] = str(row.get("subtype") or "")
    return impact


def summarize_impacts(target_types: list[str], impacts: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for object_type in target_types:
        rows = [row for row in impacts if row.get("object_type") == object_type]
        levels: dict[str, int] = {}
        for row in rows:
            level = str(row.get("risk_level") or "unknown")
            levels[level] = levels.get(level, 0) + 1
        summary[object_type] = {
            "count": len(rows),
            "critical": levels.get("critical", 0),
            "high": levels.get("high", 0),
            "medium": levels.get("medium", 0),
            "low": levels.get("low", 0),
            "max_depth_m": round(max((float(row.get("depth_m") or 0) for row in rows), default=0), 3),
        }
    return summary


def affected_object_ids(target_types: list[str], impacts: list[dict[str, Any]],
                        limit: int | None = None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for object_type in target_types:
        ids: list[str] = []
        seen: set[str] = set()
        for row in impacts:
            if row.get("object_type") != object_type:
                continue
            object_id = str(row.get("object_id") or "")
            if not object_id or object_id in seen:
                continue
            ids.append(object_id)
            seen.add(object_id)
            if limit is not None and len(ids) >= limit:
                break
        result[object_type] = ids
    return result


def safe_row_point(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        return row_point(row)
    except (TypeError, ValueError):
        return None


def risk_rank(level: str) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(level, 0)
