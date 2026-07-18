from __future__ import annotations

from typing import Any

from .common import id_field
from .forecast import (
    LATEST_FORECAST_ID,
    compact_cell_index,
    nearest_cell,
    query_forecast_cells,
    risk_level,
    row_point,
    sampled_geometry_points,
)


POINT_TARGET_TYPES = ("Facility", "Bridge", "Transfer", "Place")
LINE_TARGET_TYPES = ("Road", "Route")
TARGET_TYPES = POINT_TARGET_TYPES + LINE_TARGET_TYPES


def analyze_inundation_impacts(resolver, forecast_id: str = "latest",
                               target_type: str = "all",
                               min_depth_m: float = 0.15,
                               max_distance_m: float = 120.0) -> dict[str, Any]:
    forecast_key = LATEST_FORECAST_ID if forecast_id in ("", "latest") else forecast_id
    target_types = resolve_target_types(target_type)
    if not target_types:
        return {
            "status": "invalid_target_type",
            "forecast_id": forecast_key,
            "target_type": target_type,
            "valid_target_types": ["all", *TARGET_TYPES],
            "summary": {},
            "total_impacts": 0,
            "impacts": [],
        }

    cells = query_forecast_cells(resolver, {"forecast_id": forecast_key})
    if not cells:
        return {
            "status": "no_forecast_cells",
            "forecast_id": forecast_key,
            "target_type": target_type,
            "summary": {item: 0 for item in target_types},
            "total_impacts": 0,
            "impacts": [],
            "basis": "未找到可用于叠加分析的预测淹没单元。",
        }

    cell_index = compact_cell_index(cells, min_depth=float(min_depth_m or 0))
    impacts: list[dict[str, Any]] = []
    for object_type in target_types:
        if object_type in POINT_TARGET_TYPES:
            impacts.extend(analyze_point_objects(
                resolver,
                object_type,
                cell_index,
                min_depth_m=float(min_depth_m or 0),
                max_distance_m=float(max_distance_m or 0),
            ))
        else:
            impacts.extend(analyze_linear_objects(
                resolver,
                object_type,
                cell_index,
                min_depth_m=float(min_depth_m or 0),
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
    return {
        "status": "completed",
        "forecast_id": forecast_key,
        "target_type": target_type or "all",
        "parameters": {
            "min_depth_m": float(min_depth_m or 0),
            "max_distance_m": float(max_distance_m or 0),
        },
        "summary": summary,
        "total_impacts": len(impacts),
        "impacts": impacts[:80],
        "basis": (
            "使用最新 ForecastCell 预测淹没网格执行确定性空间邻近分析；"
            "点对象按对象坐标匹配最近淹没网格，线对象按几何采样点匹配最深命中网格。"
        ),
        "mappable": [
            {"object_type": "ForecastCell", "filters": {"forecast_id": "latest"}},
            *[
                {
                    "object_type": object_type,
                    "filters": {},
                    "object_ids": [
                        row["object_id"] for row in impacts
                        if row.get("object_type") == object_type and row.get("object_id")
                    ][:20],
                }
                for object_type in target_types
            ],
        ],
    }


def resolve_target_types(target_type: str) -> list[str]:
    value = str(target_type or "all").strip()
    if not value or value.lower() == "all":
        return list(TARGET_TYPES)
    aliases = {
        "facility": "Facility",
        "bridge": "Bridge",
        "transfer": "Transfer",
        "place": "Place",
        "road": "Road",
        "route": "Route",
    }
    canonical = aliases.get(value.lower(), value)
    return [canonical] if canonical in TARGET_TYPES else []


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
        impacts.append(make_impact(object_type, row, object_id_field, cell, "point_nearest_cell"))
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
            nearest_cell(point, cell_index, max_distance_m=max_distance_m)
            for point in points
        ]
        matched = [item for item in matched if item and float(item.get("depth_m") or 0) >= min_depth_m]
        if not matched:
            continue
        deepest = max(matched, key=lambda item: float(item.get("depth_m") or 0))
        impact = make_impact(object_type, row, object_id_field, deepest, "line_sample_nearest_cell")
        impact["sample_hits"] = len(matched)
        impacts.append(impact)
    return impacts


def make_impact(object_type: str, row: dict[str, Any], object_id_field: str,
                cell: dict[str, Any], basis: str) -> dict[str, Any]:
    depth = float(cell.get("depth_m") or 0)
    velocity = float(cell.get("velocity_mps") or 0)
    return {
        "object_type": object_type,
        "object_id": str(row.get(object_id_field) or ""),
        "name": row.get("name") or row.get(object_id_field) or "",
        "risk_level": cell.get("risk_level") or risk_level(depth, velocity),
        "depth_m": round(depth, 3),
        "velocity_mps": round(velocity, 3),
        "distance_m": round(float(cell.get("_distance_m") or 0), 1),
        "forecast_cell_id": cell.get("forecast_cell_id", ""),
        "mesh_cell_id": cell.get("mesh_cell_id", ""),
        "basis": basis,
    }


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
