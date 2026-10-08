from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from shapely import union_all
from shapely.geometry import shape

from .forecast_constants import LATEST_FORECAST_ID
from .forecast_geometry import distance_m, iter_coords, row_point
from .linear_inundation import WetCellIndex, metric_geometry
from .hydrodynamic_grid import (
    MESH_DB_PATH,
    forecast_series_path,
    forecast_time_steps,
    offset_time_iso,
)


DEFAULT_BLOCKED_DEPTH_M = 0.30
DEFAULT_HORIZON_H = 24.0
DEFAULT_WALK_SPEED_MPS = 1.0


def analyze_latest_evacuation_time(
    resolver,
    evacuation_unit_id: str = "",
    evacuation_unit_name: str = "",
    evacuation_route_id: str = "",
    forecast_id: str = "latest",
    blocked_depth_m: float | None = None,
    clearance_duration_min: float | str | None = None,
    safety_buffer_min: float = 0.0,
) -> dict[str, Any]:
    """Calculate the latest confirmed evacuation window from a depth series.

    The deadline is deliberately based on the last model time slice that is
    confirmed passable, rather than interpolating a threshold crossing between
    two model slices.  This keeps the result deterministic and conservative.
    """
    evacuation_unit, unit_error = resolve_evacuation_unit(
        resolver,
        evacuation_unit_id=evacuation_unit_id,
        evacuation_unit_name=evacuation_unit_name,
    )
    if unit_error:
        return unit_error

    route, route_error = resolve_route(
        resolver,
        evacuation_unit,
        evacuation_route_id=evacuation_route_id,
    )
    if route_error:
        return route_error

    route_points = geometry_points(route)
    if len(route_points) < 2 or metric_geometry(route, ("LineString",)) is None:
        return error_result(
            "invalid_route_geometry",
            "关联转移路线缺少可分析的线几何。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
        )

    forecast_context = resolve_forecast_context(resolver, forecast_id)
    if not forecast_context.get("forecast_id") or not forecast_context.get("valid_from"):
        return error_result("forecast_unavailable", "指定预测缺少可用的版本或时间基准。", forecast_id=forecast_id)
    forecast_id = forecast_context["forecast_id"]
    series_path = forecast_series_path(forecast_id)
    time_steps = forecast_time_steps(forecast_id)
    if not series_path.exists() or not time_steps:
        return error_result(
            "no_forecast_series",
            "当前演进工作空间没有可用的多时刻水深预测序列。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )

    try:
        series = np.load(series_path, mmap_mode="r")
    except (OSError, ValueError) as exc:
        return error_result(
            "invalid_forecast_series",
            f"无法读取多时刻水深预测序列：{exc}",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )
    if series.ndim != 2 or series.shape[0] == 0 or series.shape[1] == 0:
        return error_result(
            "invalid_forecast_series",
            "多时刻水深预测序列维度无效。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
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
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
        )
    if selected_steps[-1][1] < DEFAULT_HORIZON_H:
        return error_result(
            "incomplete_forecast_horizon",
            "当前多时刻水深序列不足24小时，不能据此形成24小时最晚转移时间结论。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
            available_horizon_h=selected_steps[-1][1],
            required_horizon_h=DEFAULT_HORIZON_H,
        )

    destination = resolve_destination(resolver, route)
    coverage = match_component_mesh_cells(
        analysis_component_geometries(evacuation_unit, route, destination),
        mesh_path=MESH_DB_PATH, forecast_cell_count=int(series.shape[1]),
    )
    component_cells = {name: item["cell_ids"] for name, item in coverage.items()}
    incomplete = [name for name, item in coverage.items() if not item["fully_covered"]]
    all_cell_ids = sorted({
        cell_id
        for cell_ids in component_cells.values()
        for cell_id in cell_ids
    })
    if not all_cell_ids:
        return error_result(
            "no_matching_mesh_cells",
            "转移起点、路线和安置点附近没有匹配到水动力网格。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route=evacuation_route_summary(route),
            forecast_id=normalize_result_forecast_id(forecast_id),
            coverage=coverage,
        )

    values = np.asarray(series[np.ix_([index for index, _ in selected_steps], np.asarray(all_cell_ids) - 1)])
    if not np.isfinite(values).all() or (values < 0).any():
        return error_result("invalid_forecast_series", "匹配网格包含无效水深，无法计算转移窗口。", forecast_id=forecast_id)

    route_threshold = route.get("blocked_depth_m")
    if route_threshold is None:
        route_threshold = 0.15 if route.get("profile") == "foot" else DEFAULT_BLOCKED_DEPTH_M
    threshold = max(0.0, float(route_threshold if blocked_depth_m in (None, "") else blocked_depth_m))
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

    if incomplete:
        # Keep observed unsafe evidence, but never infer a safe deadline for
        # components (or portions of the route) outside the model coverage.
        deadline.update(
            deadline_status="incomplete_coverage", last_confirmed_safe_time_h=None,
            latest_safe_completion_time_h=None, latest_departure_time_h=None,
            message="模型未完整覆盖起点、路线或终点，只能报告已覆盖部分的风险，不能确定完整转移窗口。",
        )
    attach_absolute_times(deadline, forecast_context.get("valid_from"))
    attach_remaining_time(deadline, forecast_context)

    limitations = []
    if incomplete:
        limitations.append("模型覆盖不完整：" + "、".join(incomplete) + "；未覆盖不代表无洪水。")
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
        "status": "partial" if incomplete else "completed",
        "deadline_status": deadline["deadline_status"],
        "forecast_id": forecast_context.get("forecast_id")
        or normalize_result_forecast_id(forecast_id),
        "forecast_window": {
            "window_start": forecast_context.get("window_start"),
            "forecast_time": forecast_context.get("forecast_time"),
            "valid_from": forecast_context.get("valid_from"),
            "valid_to": forecast_context.get("valid_to"),
            "simulation_time": forecast_context.get("simulation_time"),
            "observed_through": forecast_context.get("observed_through"),
            "horizon_h": DEFAULT_HORIZON_H,
            "first_time_h": timeline[0]["time_h"],
            "last_time_h": timeline[-1]["time_h"],
            "time_step_count": len(timeline),
        },
        "evacuation_unit": evacuation_unit_summary(evacuation_unit),
        "evacuation_route": {
            **evacuation_route_summary(route),
            "length_m": round(route_length_m(route_points), 1),
            "matched_mesh_cell_count": len(component_cells.get("route", [])),
        },
        "destination_site": evacuation_site_summary(destination),
        "parameters": {
            "blocked_depth_m": threshold,
            "clearance_duration_min": round(duration["minutes"], 2),
            "clearance_duration_source": duration["source"],
            "safety_buffer_min": buffer_min,
            "spatial_method": "full_geometry_polygon_intersection",
        },
        "coverage": coverage,
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
                "valid_at": absolute_time(
                    forecast_context.get("valid_from"), row["time_h"],
                ),
                "max_depth_m": row["max_depth_m"],
                "component_depths_m": row["component_depths_m"],
                "unsafe_components": row["unsafe_components"],
            } for row in timeline],
        },
        "basis": (
            "逐时读取指定预测版本0至24小时水深序列，将转移起点、完整路线和终点与网格多边形求交；"
            "任一已覆盖部分达到禁行水深即记录不可通行。覆盖完整时，截止时间采用首次不可通行前的"
            "最后一个确认安全时间切片，不对两个时间切片之间的阈值到达时刻作插值。"
        ),
        "limitations": limitations,
    }


def resolve_evacuation_unit(
    resolver,
    *,
    evacuation_unit_id: str,
    evacuation_unit_name: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if evacuation_unit_id:
        row = resolver.query_by_id("EvacuationUnit", evacuation_unit_id)
        if row:
            return row, None
        return None, error_result(
            "evacuation_unit_not_found",
            f"未找到转移单元 {evacuation_unit_id}。",
            evacuation_unit_id=str(evacuation_unit_id),
        )

    name = str(evacuation_unit_name or "").strip()
    if not name:
        return None, error_result(
            "evacuation_unit_required",
            "必须提供 evacuation_unit_id 或 evacuation_unit_name。",
        )
    rows = resolver.query("EvacuationUnit")
    exact = [
        row for row in rows
        if name in {
            str(row.get("name") or "").strip(),
            str(row.get("source_name") or "").strip(),
        }
    ]
    matches = exact or [
        row for row in rows
        if name in str(row.get("name") or "")
        or str(row.get("name") or "") in name
        or name in str(row.get("source_name") or "")
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, error_result(
            "evacuation_unit_not_found",
            f"未找到名称为{name}的转移单元。",
            evacuation_unit_name=name,
        )
    return None, error_result(
        "ambiguous_evacuation_unit",
        f"名称{name}匹配到多个转移单元，请改用 evacuation_unit_id。",
        evacuation_unit_name=name,
        candidates=[evacuation_unit_summary(row) for row in matches[:10]],
    )


def resolve_route(
    resolver,
    evacuation_unit: dict[str, Any],
    *,
    evacuation_route_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    selected_id = str(evacuation_route_id or "")
    if selected_id:
        row = resolver.query_by_id("EvacuationRoute", selected_id)
        if row:
            unit_id = str(evacuation_unit.get("evacuation_unit_id") or "")
            origins = [str(row[key]) for key in ("origin_unit_id",) if row.get(key)]
            if row.get("start_object_type") == "EvacuationUnit" and row.get("start_object_id"):
                origins.append(str(row["start_object_id"]))
            points = geometry_points(row)
            start = safe_row_point({"longitude": row.get("start_lon"), "latitude": row.get("start_lat")})
            start = start or (points[0] if points else None)
            origin = safe_row_point(evacuation_unit)
            matches = all(ident == unit_id for ident in origins) if origins else bool(
                origin and start and distance_m(origin, start) <= 10,
            )
            if not matches:
                return None, error_result("route_origin_mismatch", "指定路线的起点与转移单元不一致，请选择对应路线。",
                                          evacuation_route_id=selected_id, evacuation_unit_id=unit_id)
            return row, None
        return None, error_result(
            "route_not_found",
            f"未找到转移单元关联的路线 {selected_id}。",
            evacuation_unit=evacuation_unit_summary(evacuation_unit),
            evacuation_route_id=selected_id,
        )

    unit_id = str(evacuation_unit.get("evacuation_unit_id") or "")
    rows = [
        row for row in resolver.query("EvacuationRoute")
        if str(row.get("origin_unit_id") or "") == unit_id
        or (
            row.get("start_object_type") == "EvacuationUnit"
            and str(row.get("start_object_id") or "") == unit_id
        )
    ]
    if rows:
        rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
        return rows[0], None
    return None, error_result(
        "route_required",
        "该转移单元没有关联的预定转移路线。",
        evacuation_unit=evacuation_unit_summary(evacuation_unit),
    )


def resolve_destination(resolver, route: dict[str, Any]) -> dict[str, Any] | None:
    site_id = str(route.get("destination_site_id") or "")
    return resolver.query_by_id("EvacuationSite", site_id) if site_id else None


def geometry_points(row: dict[str, Any]) -> list[tuple[float, float]]:
    try:
        geometry = row.get("geometry") or {}
        geometry = json.loads(geometry) if isinstance(geometry, str) else geometry
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return []
    return iter_coords(geometry.get("coordinates") or [])


def safe_row_point(row: dict[str, Any] | None) -> tuple[float, float] | None:
    if not row:
        return None
    try:
        point = row_point(row)
        return point if point and all(math.isfinite(value) for value in point) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def analysis_component_geometries(transfer: dict, route: dict, destination: dict | None) -> dict:
    def point_row(row):
        point = safe_row_point(row)
        return {"geometry": {"type": "Point", "coordinates": point}} if point else {}
    # Routes planned to explicit coordinates need no shelter library record.
    endpoint = destination or {"longitude": route.get("destination_lon"), "latitude": route.get("destination_lat")}
    return {"origin": point_row(transfer), "route": route, "destination": point_row(endpoint)}


def match_component_mesh_cells(components: dict, *, mesh_path: Path,
                               forecast_cell_count: int) -> dict:
    geometries = {name: metric_geometry(row, ("Point", "LineString", "MultiLineString"))
                  for name, row in components.items()}
    bounds = []
    for name, row in components.items():
        if geometries[name] is not None:
            raw = row["geometry"]
            bounds.append(shape(json.loads(raw) if isinstance(raw, str) else raw).bounds)
    cells = []
    if bounds and mesh_path.is_file():
        # Only read mesh triangles in the bounding box; no vertex sampling or
        # centroid-distance cutoff. Project with the shared impact geometry.
        with closing(sqlite3.connect(mesh_path)) as conn:
            rows = conn.execute(
                "select cell_id, lon1, lat1, lon2, lat2, lon3, lat3 from cells "
                "where max_lon >= ? and min_lon <= ? and max_lat >= ? and min_lat <= ?",
                (min(b[0] for b in bounds), max(b[2] for b in bounds),
                 min(b[1] for b in bounds), max(b[3] for b in bounds)),
            )
            for row in rows:
                if not 1 <= int(row[0]) <= forecast_cell_count:
                    continue
                ring = [[row[1], row[2]], [row[3], row[4]], [row[5], row[6]], [row[1], row[2]]]
                cells.append({"mesh_cell_id": int(row[0]), "depth_m": 1,
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})
    index = WetCellIndex(cells, 0)
    result = {}
    for name, geometry in geometries.items():
        hits = [] if geometry is None else index.tree.query(geometry, predicate="intersects")
        ids = sorted(index.rows[int(i)]["mesh_cell_id"] for i in hits)
        covered = union_all([index.geometries[int(i)] for i in hits])
        missing_length = geometry.difference(covered).length if geometry is not None and name == "route" else None
        # Numerical seams between projected triangle edges can be sub-mm.
        fully_covered = bool(ids) and (missing_length <= 0.001 if name == "route" else covered.covers(geometry))
        result[name] = {"cell_ids": ids, "matched_cell_count": len(ids),
                        "fully_covered": bool(fully_covered),
                        "status": "covered" if fully_covered else "missing_geometry" if geometry is None else "outside_or_partial_mesh",
                        "uncovered_length_m": round(missing_length, 3) if missing_length is not None else None}
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
                else None
            )
            component_depths[name] = round(depth, 4) if depth is not None else None
            if depth is not None and depth >= blocked_depth_m:
                unsafe_components.append(name)
        max_depth = max((value for value in component_depths.values() if value is not None), default=None)
        timeline.append({
            "time_h": round(time_h, 3),
            "max_depth_m": round(max_depth, 4) if max_depth is not None else None,
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


def resolve_forecast_context(resolver, forecast_id: str = "latest") -> dict[str, Any]:
    try:
        filters = {} if forecast_id in ("", "latest", LATEST_FORECAST_ID) else {"forecast_id": forecast_id}
        rows = resolver.query("FloodForecast", filters=filters, order_by="-forecast_sequence", limit=1)
    except (FileNotFoundError, TypeError, ValueError):
        rows = []
    run = rows[-1] if rows else {}
    if filters and run.get("forecast_id") != forecast_id:
        return {}
    try:
        boundary_flow = run.get("boundary_flow") or {}
        boundary_flow = json.loads(boundary_flow) if isinstance(boundary_flow, str) else boundary_flow
    except (TypeError, json.JSONDecodeError):
        boundary_flow = {}
    return {
        "forecast_id": str(run.get("forecast_id") or ""),
        "forecast_time": str(
            run.get("forecast_time")
            or boundary_flow.get("simulation_time")
            or boundary_flow.get("triggered_at")
            or ""
        ) or None,
        "valid_from": str(
            run.get("valid_from")
            or boundary_flow.get("window_start")
            or ""
        ) or None,
        "valid_to": str(
            run.get("valid_to")
            or boundary_flow.get("window_end")
            or ""
        ) or None,
        "window_start": str(
            run.get("valid_from")
            or boundary_flow.get("window_start")
            or ""
        ) or None,
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
    return offset_time_iso(window_start, time_h)


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


def evacuation_unit_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "evacuation_unit_id": str(row.get("evacuation_unit_id") or ""),
        "name": str(row.get("name") or ""),
        "source_name": str(row.get("source_name") or ""),
        "population": int(row.get("population") or 0),
        "planned_flood_arrival_window": row.get("flood_arrival_window"),
    }


def evacuation_route_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "evacuation_route_id": str(row.get("evacuation_route_id") or ""),
        "name": str(row.get("name") or ""),
        "route_type": str(row.get("route_type") or ""),
        "origin_unit_id": str(row.get("origin_unit_id") or ""),
        "destination_site_id": str(row.get("destination_site_id") or ""),
    }


def evacuation_site_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "evacuation_site_id": str(row.get("evacuation_site_id") or ""),
        "name": str(row.get("name") or ""),
        "site_type": str(row.get("site_type") or ""),
    }


def error_result(status: str, error: str, **values: Any) -> dict[str, Any]:
    return {"status": status, "error": error, **values}
