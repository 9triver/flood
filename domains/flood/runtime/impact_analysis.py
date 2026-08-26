from __future__ import annotations

import json
import math
from typing import Any

from .common import id_field
from .forecast import (
    LATEST_FORECAST_ID,
    build_cell_spatial_index,
    compact_cell_index,
    iter_coords,
    nearby_cells,
    nearest_cell,
    point_segment_distance_m,
    query_forecast_cells,
    risk_level,
    row_point,
    sampled_geometry_points,
)
from .hydrodynamic_grid import forecast_time_context


POINT_TARGET_TYPES = ("Facility", "EvacuationUnit", "EvacuationSite")
LINE_TARGET_TYPES = ("Road", "EvacuationRoute")
TARGET_TYPES = ("Facility", "Bridge", "EvacuationUnit", "EvacuationSite", *LINE_TARGET_TYPES)
BRIDGE_INFLUENCE_RADIUS_M = 80.0


def analyze_inundation_impacts(
    resolver,
    forecast_id: str = "latest",
    target_type: str = "all",
    min_depth_m: float = 0.15,
    max_distance_m: float = 10.0,
    time_h: float | None = None,
    bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
) -> dict[str, Any]:
    forecast_key = LATEST_FORECAST_ID if forecast_id in ("", "latest") else forecast_id
    analysis_time_h = coerce_time_h(time_h)
    cell_filters: dict[str, Any] = {"forecast_id": forecast_key}
    if analysis_time_h is not None:
        cell_filters["time_h"] = analysis_time_h
    cells = (
        query_forecast_cells(resolver, cell_filters)
        if resolve_target_types(target_type)
        else []
    )
    return analyze_inundation_cells(
        resolver,
        cells,
        forecast_id=forecast_key,
        target_type=target_type,
        min_depth_m=min_depth_m,
        max_distance_m=max_distance_m,
        time_h=analysis_time_h,
        bridge_influence_radius_m=bridge_influence_radius_m,
    )


def analyze_inundation_cells(
    resolver,
    cells: list[dict[str, Any]],
    *,
    forecast_id: str,
    target_type: str = "all",
    min_depth_m: float = 0.15,
    max_distance_m: float = 10.0,
    time_h: float | None = None,
    bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
    time_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze explicit forecast cells without relying on a latest workspace."""

    forecast_key = str(forecast_id or "").strip()
    if not forecast_key:
        raise ValueError("forecast_id must not be empty")
    analysis_time_h = coerce_time_h(time_h)
    target_types = resolve_target_types(target_type)
    if not target_types:
        return {
            "status": "invalid_target_type",
            "forecast_id": forecast_key,
            "time_h": analysis_time_h,
            "target_type": target_type,
            "valid_target_types": ["all", *TARGET_TYPES],
            "summary": {},
            "total_impacts": 0,
            "impacts": [],
            **_time_fields(forecast_key, analysis_time_h, time_context),
        }

    if not cells:
        time_fields = _time_fields(forecast_key, analysis_time_h, time_context)
        return {
            "status": "no_forecast_cells",
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
    cell_index = compact_cell_index(cells, min_depth=minimum_depth)
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
    for object_type in target_types:
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
            impacts.extend(analyze_linear_objects(
                resolver,
                object_type,
                cell_index,
                min_depth_m=minimum_depth,
                max_distance_m=float(max_distance_m or 0),
            ))

    impacts = sorted(
        impacts,
        key=lambda row: (
            -risk_rank(str(row.get("risk_level") or "")),
            -float(row.get("depth_m") or 0),
            float(row.get("distance_m") or 0),
        ),
    )
    summary = summarize_impacts(target_types, impacts)
    actual_time_h = actual_cell_time_h(cells, analysis_time_h)
    time_fields = _time_fields(resolved_forecast_id, actual_time_h, time_context)
    return {
        "status": "completed",
        "forecast_id": resolved_forecast_id,
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
        "basis": analysis_basis(actual_time_h, time_fields.get("analysis_time_at")),
        "impacts": impacts,
    }


def _time_fields(
    forecast_id: str,
    time_h: float | None,
    time_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if time_context is None:
        return analysis_time_fields(forecast_id, time_h)
    return {
        "forecast_time": time_context.get("forecast_time"),
        "valid_from": time_context.get("valid_from"),
        "valid_to": time_context.get("valid_to"),
        "analysis_time_at": time_context.get("analysis_time_at"),
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
        value = coerce_time_h(cell.get("lead_time_h"))
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
        "线对象按几何采样点匹配最深命中网格。"
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
        "route": "EvacuationRoute",
    }
    canonical = aliases.get(value.lower(), value)
    return [canonical] if canonical in TARGET_TYPES else []


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
            "nearby_max_velocity_mps": round(
                float(deepest.get("velocity_mps") or 0),
                3,
            ),
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
                           max_distance_m: float) -> list[dict[str, Any]]:
    impacts = []
    object_id_field = id_field(object_type)
    for row in resolver.query(object_type):
        points = safe_sampled_geometry_points(row, max_points=20)
        if not points:
            continue
        matched = [
            (point, nearest_cell(point, cell_index, max_distance_m=max_distance_m))
            for point in points
        ]
        matched = [
            (point, cell) for point, cell in matched
            if cell and float(cell.get("depth_m") or 0) >= min_depth_m
        ]
        if not matched:
            continue
        impact_point, deepest = max(
            matched,
            key=lambda item: float(item[1].get("depth_m") or 0),
        )
        impact = make_impact(
            object_type,
            row,
            object_id_field,
            deepest,
            "line_sample_nearest_cell",
            impact_point,
        )
        impact["sample_hits"] = len(matched)
        impacts.append(impact)
    return impacts


def make_impact(object_type: str, row: dict[str, Any], object_id_field: str,
                cell: dict[str, Any], basis: str,
                impact_point: tuple[float, float]) -> dict[str, Any]:
    depth = float(cell.get("depth_m") or 0)
    velocity = float(cell.get("velocity_mps") or 0)
    impact = {
        "object_type": object_type,
        "object_id": str(row.get(object_id_field) or ""),
        "name": row.get("name") or row.get(object_id_field) or "",
        "risk_level": cell.get("risk_level") or risk_level(depth, velocity),
        "depth_m": round(depth, 3),
        "velocity_mps": round(velocity, 3),
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


def safe_sampled_geometry_points(row: dict[str, Any],
                                 max_points: int) -> list[tuple[float, float]]:
    try:
        return sampled_geometry_points(row, max_points=max_points)
    except (TypeError, ValueError):
        return []


def risk_rank(level: str) -> int:
    return {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(level, 0)
