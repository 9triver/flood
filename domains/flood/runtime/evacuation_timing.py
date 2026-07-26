from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from .forecast import LATEST_FORECAST_ID, distance_m, iter_coords, row_point
from .hydrodynamic_grid import (
    MESH_DB_PATH,
    forecast_series_path,
    forecast_time_steps,
)


DEFAULT_BLOCKED_DEPTH_M = 0.30
DEFAULT_HORIZON_H = 24.0
DEFAULT_ROUTE_MATCH_DISTANCE_M = 10.0
DEFAULT_WALK_SPEED_MPS = 1.0


def analyze_latest_evacuation_time(
    resolver,
    transfer_id: str = "",
    transfer_name: str = "",
    route_id: str = "",
    forecast_id: str = "latest",
    blocked_depth_m: float = DEFAULT_BLOCKED_DEPTH_M,
    clearance_duration_min: float | str | None = None,
    safety_buffer_min: float = 0.0,
) -> dict[str, Any]:
    """Calculate the latest confirmed evacuation window from a depth series.

    The deadline is deliberately based on the last model time slice that is
    confirmed passable, rather than interpolating a threshold crossing between
    two model slices.  This keeps the result deterministic and conservative.
    """
    transfer, transfer_error = resolve_transfer(
        resolver, transfer_id=transfer_id, transfer_name=transfer_name,
    )
    if transfer_error:
        return transfer_error

    route, route_error = resolve_route(
        resolver, transfer, route_id=route_id,
    )
    if route_error:
        return route_error

    route_points = geometry_points(route)
    if len(route_points) < 2:
        return error_result(
            "invalid_route_geometry",
            "关联转移路线缺少可分析的线几何。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
        )

    series_path = forecast_series_path(forecast_id)
    time_steps = forecast_time_steps(forecast_id)
    if not series_path.exists() or not time_steps:
        return error_result(
            "no_forecast_series",
            "当前演进工作空间没有可用的多时刻水深预测序列。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )

    try:
        series = np.load(series_path, mmap_mode="r")
    except (OSError, ValueError) as exc:
        return error_result(
            "invalid_forecast_series",
            f"无法读取多时刻水深预测序列：{exc}",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )
    if series.ndim != 2 or series.shape[0] == 0 or series.shape[1] == 0:
        return error_result(
            "invalid_forecast_series",
            "多时刻水深预测序列维度无效。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )

    selected_steps = [
        (index, float(time_h))
        for index, time_h in enumerate(time_steps[: int(series.shape[0])])
        if 0 <= float(time_h) <= DEFAULT_HORIZON_H
    ]
    if not selected_steps:
        return error_result(
            "no_forecast_time_steps",
            "预测序列中没有0至24小时的有效时间切片。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )
    if selected_steps[-1][1] < DEFAULT_HORIZON_H:
        return error_result(
            "incomplete_forecast_horizon",
            "当前多时刻水深序列不足24小时，不能据此形成24小时最晚转移时间结论。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
            available_horizon_h=selected_steps[-1][1],
            required_horizon_h=DEFAULT_HORIZON_H,
        )

    destination = resolve_destination(resolver, transfer, route)
    components = analysis_component_points(
        transfer, route_points, destination,
    )
    component_cells = match_component_mesh_cells(
        components,
        mesh_path=MESH_DB_PATH,
        max_distance_m=DEFAULT_ROUTE_MATCH_DISTANCE_M,
    )
    component_cells = {
        name: [
            cell_id for cell_id in cell_ids
            if 1 <= cell_id <= int(series.shape[1])
        ]
        for name, cell_ids in component_cells.items()
    }
    all_cell_ids = sorted({
        cell_id
        for cell_ids in component_cells.values()
        for cell_id in cell_ids
    })
    if not all_cell_ids:
        return error_result(
            "no_matching_mesh_cells",
            "转移起点、路线和安置点附近没有匹配到水动力网格。",
            transfer=transfer_summary(transfer),
            route=route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )

    threshold = max(0.0, float(blocked_depth_m or 0))
    duration = resolve_clearance_duration(
        route, route_points, clearance_duration_min,
    )
    buffer_min = max(0.0, float(safety_buffer_min or 0))
    timeline = build_depth_timeline(
        series,
        selected_steps,
        component_cells,
        blocked_depth_m=threshold,
    )
    first_unsafe_index = next(
        (index for index, row in enumerate(timeline) if row["unsafe"]),
        None,
    )
    deadline = build_deadline(
        timeline,
        first_unsafe_index,
        clearance_duration_min=duration["minutes"],
        safety_buffer_min=buffer_min,
    )

    forecast_context = resolve_forecast_context(resolver)
    attach_absolute_times(deadline, forecast_context.get("window_start"))
    attach_remaining_time(deadline, forecast_context)

    limitations = []
    if duration["source"] != "user_provided_clearance_duration":
        limitations.append(
            "最晚出发时刻使用路线单程通行时间，不包含全体人员集结、分批运输和清点耗时；"
            "如需形成行动指令，应传入经核定的 clearance_duration_min。"
        )
    if buffer_min == 0:
        limitations.append(
            "当前结果未额外扣除安全提前量；如有本地预案要求，应通过 safety_buffer_min 纳入。"
        )

    return {
        "status": "completed",
        "deadline_status": deadline["deadline_status"],
        "forecast_id": forecast_context.get("forecast_id")
        or normalize_result_forecast_id(forecast_id),
        "forecast_window": {
            "window_start": forecast_context.get("window_start"),
            "simulation_time": forecast_context.get("simulation_time"),
            "observed_through": forecast_context.get("observed_through"),
            "horizon_h": DEFAULT_HORIZON_H,
            "first_time_h": timeline[0]["time_h"],
            "last_time_h": timeline[-1]["time_h"],
            "time_step_count": len(timeline),
        },
        "transfer": transfer_summary(transfer),
        "route": {
            **route_summary(route),
            "length_m": round(route_length_m(route_points), 1),
            "matched_mesh_cell_count": len(component_cells.get("route", [])),
        },
        "destination": place_summary(destination),
        "parameters": {
            "blocked_depth_m": threshold,
            "clearance_duration_min": round(duration["minutes"], 2),
            "clearance_duration_source": duration["source"],
            "safety_buffer_min": buffer_min,
            "route_match_distance_m": DEFAULT_ROUTE_MATCH_DISTANCE_M,
        },
        "deadline": deadline,
        "evidence": {
            "first_unsafe_components": deadline.get("first_unsafe_components", []),
            "first_unsafe_max_depth_m": deadline.get("first_unsafe_max_depth_m"),
            "matched_mesh_cells": {
                name: len(cell_ids)
                for name, cell_ids in component_cells.items()
            },
            "depth_timeline": [{
                "time_h": row["time_h"],
                "max_depth_m": row["max_depth_m"],
                "unsafe_components": row["unsafe_components"],
            } for row in timeline],
        },
        "basis": (
            "逐时读取当前工作空间内0至24小时水深序列，匹配转移起点、预定路线和安置点附近"
            "水动力网格；任一部分达到禁行水深即判为不可通行。截止时间采用首次不可通行前的"
            "最后一个确认安全时间切片，不对两个时间切片之间的阈值到达时刻作插值。"
        ),
        "limitations": limitations,
    }


def resolve_transfer(resolver, *, transfer_id: str,
                     transfer_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if transfer_id:
        row = resolver.query_by_id("Transfer", transfer_id)
        if row:
            return row, None
        return None, error_result(
            "transfer_not_found", f"未找到转移安排 {transfer_id}。",
            transfer_id=str(transfer_id),
        )

    name = str(transfer_name or "").strip()
    if not name:
        return None, error_result(
            "transfer_required", "必须提供 transfer_id 或 transfer_name。",
        )
    rows = resolver.query("Transfer")
    exact = [row for row in rows if str(row.get("name") or "").strip() == name]
    matches = exact or [
        row for row in rows
        if name in str(row.get("name") or "")
        or str(row.get("name") or "") in name
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, error_result(
            "transfer_not_found", f"未找到名称为{name}的转移安排。",
            transfer_name=name,
        )
    return None, error_result(
        "ambiguous_transfer",
        f"名称{name}匹配到多个转移安排，请改用 transfer_id。",
        transfer_name=name,
        candidates=[transfer_summary(row) for row in matches[:10]],
    )


def resolve_route(resolver, transfer: dict[str, Any], *,
                  route_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    selected_id = str(route_id or transfer.get("route_id") or "")
    if selected_id:
        row = resolver.query_by_id("Route", selected_id)
        if row:
            return row, None
        return None, error_result(
            "route_not_found",
            f"未找到转移安排关联的路线 {selected_id}。",
            transfer=transfer_summary(transfer),
            route_id=selected_id,
        )

    transfer_id = str(transfer.get("transfer_id") or "")
    rows = [
        row for row in resolver.query("Route")
        if str(row.get("transfer_id") or row.get("start_object_id") or "")
        == transfer_id
    ]
    if rows:
        rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
        return rows[0], None
    return None, error_result(
        "route_required",
        "该转移安排没有关联的预定转移路线。",
        transfer=transfer_summary(transfer),
    )


def resolve_destination(resolver, transfer: dict[str, Any],
                        route: dict[str, Any]) -> dict[str, Any] | None:
    place_id = str(route.get("place_id") or transfer.get("place_id") or "")
    return resolver.query_by_id("Place", place_id) if place_id else None


def geometry_points(row: dict[str, Any]) -> list[tuple[float, float]]:
    try:
        geometry = json.loads(str(row.get("geometry") or "{}"))
    except (TypeError, json.JSONDecodeError):
        return []
    return iter_coords(geometry.get("coordinates") or [])


def analysis_component_points(
    transfer: dict[str, Any],
    route_points: list[tuple[float, float]],
    destination: dict[str, Any] | None,
) -> dict[str, list[tuple[float, float]]]:
    components = {
        "route": densify_line(route_points),
    }
    origin = safe_row_point(transfer)
    if origin:
        components["origin"] = [origin]
    destination_point = safe_row_point(destination)
    if destination_point:
        components["destination"] = [destination_point]
    return components


def safe_row_point(row: dict[str, Any] | None) -> tuple[float, float] | None:
    if not row:
        return None
    try:
        return row_point(row)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def densify_line(points: list[tuple[float, float]],
                 spacing_m: float = 5.0) -> list[tuple[float, float]]:
    if not points:
        return []
    result = [points[0]]
    for start, end in zip(points, points[1:]):
        segment_length = distance_m(start, end)
        count = max(1, math.ceil(segment_length / max(1.0, spacing_m)))
        result.extend((
            start[0] + (end[0] - start[0]) * index / count,
            start[1] + (end[1] - start[1]) * index / count,
        ) for index in range(1, count + 1))
    return result


def match_component_mesh_cells(
    components: dict[str, list[tuple[float, float]]],
    *,
    mesh_path: Path,
    max_distance_m: float,
) -> dict[str, list[int]]:
    result = {name: [] for name in components}
    all_points = [point for points in components.values() for point in points]
    if not all_points or not mesh_path.exists():
        return result

    ref_lat = sum(point[1] for point in all_points) / len(all_points)
    lat_margin = max_distance_m / 110_540.0
    lon_scale = max(1.0, 111_320.0 * math.cos(math.radians(ref_lat)))
    lon_margin = max_distance_m / lon_scale
    min_lon = min(point[0] for point in all_points) - lon_margin
    max_lon = max(point[0] for point in all_points) + lon_margin
    min_lat = min(point[1] for point in all_points) - lat_margin
    max_lat = max(point[1] for point in all_points) + lat_margin

    with sqlite3.connect(mesh_path) as conn:
        rows = conn.execute(
            "select cell_id, lon1, lat1, lon2, lat2, lon3, lat3 "
            "from cells where max_lon >= ? and min_lon <= ? "
            "and max_lat >= ? and min_lat <= ?",
            (min_lon, max_lon, min_lat, max_lat),
        )
        for row in rows:
            cell_id = int(row[0])
            centroid = (
                (float(row[1]) + float(row[3]) + float(row[5])) / 3,
                (float(row[2]) + float(row[4]) + float(row[6])) / 3,
            )
            for name, points in components.items():
                if any(
                    distance_m(centroid, point) <= max_distance_m
                    for point in points
                ):
                    result[name].append(cell_id)
    return result


def build_depth_timeline(
    series: np.ndarray,
    selected_steps: list[tuple[int, float]],
    component_cells: dict[str, list[int]],
    *,
    blocked_depth_m: float,
) -> list[dict[str, Any]]:
    component_indices = {
        name: np.asarray(sorted({cell_id - 1 for cell_id in cell_ids}), dtype=int)
        for name, cell_ids in component_cells.items()
    }
    timeline = []
    for time_index, time_h in selected_steps:
        component_depths = {}
        unsafe_components = []
        for name, indices in component_indices.items():
            depth = (
                float(np.asarray(series[time_index, indices]).max())
                if indices.size
                else 0.0
            )
            component_depths[name] = round(depth, 4)
            if depth >= blocked_depth_m:
                unsafe_components.append(name)
        max_depth = max(component_depths.values(), default=0.0)
        timeline.append({
            "time_h": round(time_h, 3),
            "max_depth_m": round(max_depth, 4),
            "component_depths_m": component_depths,
            "unsafe": bool(unsafe_components),
            "unsafe_components": unsafe_components,
        })
    return timeline


def resolve_clearance_duration(
    route: dict[str, Any],
    route_points: list[tuple[float, float]],
    requested_minutes: float | str | None,
) -> dict[str, Any]:
    if requested_minutes not in (None, ""):
        return {
            "minutes": max(0.0, float(requested_minutes)),
            "source": "user_provided_clearance_duration",
        }
    try:
        duration_s = float(route.get("duration_s") or 0)
    except (TypeError, ValueError):
        duration_s = 0.0
    if duration_s > 0:
        return {
            "minutes": duration_s / 60.0,
            "source": "route_duration",
        }
    return {
        "minutes": route_length_m(route_points) / DEFAULT_WALK_SPEED_MPS / 60.0,
        "source": "estimated_route_travel_at_1_mps",
    }


def route_length_m(points: list[tuple[float, float]]) -> float:
    return sum(distance_m(start, end) for start, end in zip(points, points[1:]))


def build_deadline(
    timeline: list[dict[str, Any]],
    first_unsafe_index: int | None,
    *,
    clearance_duration_min: float,
    safety_buffer_min: float,
) -> dict[str, Any]:
    if first_unsafe_index is None:
        return {
            "deadline_status": "safe_through_horizon",
            "first_unsafe_time_h": None,
            "last_confirmed_safe_time_h": timeline[-1]["time_h"],
            "latest_safe_completion_time_h": None,
            "latest_departure_time_h": None,
            "message": "24小时预测期内未达到禁行水深，预测结果没有形成转移截止时间。",
        }

    first_unsafe = timeline[first_unsafe_index]
    if first_unsafe_index == 0:
        return {
            "deadline_status": "unsafe_at_first_step",
            "first_unsafe_time_h": first_unsafe["time_h"],
            "last_confirmed_safe_time_h": None,
            "latest_safe_completion_time_h": None,
            "latest_departure_time_h": None,
            "first_unsafe_components": first_unsafe["unsafe_components"],
            "first_unsafe_max_depth_m": first_unsafe["max_depth_m"],
            "message": "首个预测时间切片已经达到禁行水深，没有确认安全的转移窗口。",
        }

    last_safe = timeline[first_unsafe_index - 1]
    duration_h = (clearance_duration_min + safety_buffer_min) / 60.0
    latest_departure_h = max(0.0, float(last_safe["time_h"]) - duration_h)
    return {
        "deadline_status": "route_becomes_unsafe",
        "first_unsafe_time_h": first_unsafe["time_h"],
        "last_confirmed_safe_time_h": last_safe["time_h"],
        "latest_safe_completion_time_h": last_safe["time_h"],
        "latest_departure_time_h": round(latest_departure_h, 3),
        "first_unsafe_components": first_unsafe["unsafe_components"],
        "first_unsafe_max_depth_m": first_unsafe["max_depth_m"],
        "message": (
            "最晚安全完成时刻采用首次不可通行前的最后一个确认安全时间切片；"
            "最晚出发时刻再扣除通行/清空耗时和安全提前量。"
        ),
    }


def resolve_forecast_context(resolver) -> dict[str, Any]:
    try:
        rows = resolver.query("ForecastRun", order_by="-forecast_sequence", limit=1)
    except (FileNotFoundError, TypeError, ValueError):
        rows = []
    run = rows[-1] if rows else {}
    try:
        boundary_flow = json.loads(str(run.get("boundary_flow") or "{}"))
    except (TypeError, json.JSONDecodeError):
        boundary_flow = {}
    return {
        "forecast_id": str(run.get("forecast_id") or ""),
        "window_start": str(boundary_flow.get("window_start") or "") or None,
        "simulation_time": str(
            boundary_flow.get("simulation_time")
            or boundary_flow.get("triggered_at")
            or boundary_flow.get("observed_through")
            or ""
        ) or None,
        "observed_through": str(
            boundary_flow.get("observed_through")
            or boundary_flow.get("simulation_time")
            or boundary_flow.get("triggered_at")
            or ""
        ) or None,
    }


def attach_absolute_times(deadline: dict[str, Any],
                          window_start: str | None) -> None:
    deadline["first_unsafe_at"] = absolute_time(
        window_start, deadline.get("first_unsafe_time_h"),
    )
    deadline["last_confirmed_safe_at"] = absolute_time(
        window_start, deadline.get("last_confirmed_safe_time_h"),
    )
    deadline["latest_safe_completion_at"] = absolute_time(
        window_start, deadline.get("latest_safe_completion_time_h"),
    )
    deadline["latest_departure_at"] = absolute_time(
        window_start, deadline.get("latest_departure_time_h"),
    )


def attach_remaining_time(deadline: dict[str, Any],
                          forecast_context: dict[str, Any]) -> None:
    observed_h = elapsed_hours(
        forecast_context.get("window_start"),
        forecast_context.get("simulation_time")
        or forecast_context.get("observed_through"),
    )
    deadline["reference_time_h"] = observed_h
    completion_h = deadline.get("latest_safe_completion_time_h")
    departure_h = deadline.get("latest_departure_time_h")
    deadline["remaining_to_completion_h"] = rounded_difference(
        completion_h, observed_h,
    )
    deadline["remaining_to_departure_h"] = rounded_difference(
        departure_h, observed_h,
    )


def absolute_time(window_start: str | None,
                  time_h: Any) -> str | None:
    if not window_start or time_h is None:
        return None
    try:
        return (
            datetime.fromisoformat(window_start)
            + timedelta(hours=float(time_h))
        ).isoformat()
    except (TypeError, ValueError):
        return None


def elapsed_hours(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        value = (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None
    return round(value, 3)


def rounded_difference(value: Any, reference: Any) -> float | None:
    if value is None or reference is None:
        return None
    return round(float(value) - float(reference), 3)


def normalize_result_forecast_id(forecast_id: str) -> str:
    return LATEST_FORECAST_ID if forecast_id in ("", "latest") else str(forecast_id)


def transfer_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "transfer_id": str(row.get("transfer_id") or ""),
        "name": str(row.get("name") or ""),
        "population": int(row.get("population") or 0),
        "planned_arrive_time_window": row.get("arrive_time_window"),
    }


def route_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "route_id": str(row.get("route_id") or ""),
        "name": str(row.get("name") or ""),
        "route_type": str(row.get("route_type") or ""),
        "place_id": str(row.get("place_id") or ""),
    }


def place_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "place_id": str(row.get("place_id") or ""),
        "name": str(row.get("name") or ""),
        "place_type": str(row.get("place_type") or ""),
    }


def error_result(status: str, error: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "error": error, **values}
