"""Read and materialize forecast runs and inundation cells."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import threading
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import apply_filters, apply_order, apply_window, rel
from .forecast_constants import LATEST_FORECAST_ID
from .forecast_geometry import triangle_area_m2
from .forecast_storage import read_forecast_runs
from .hydrodynamic_grid import (
    MESH_DB_PATH,
    coerce_optional_float,
    forecast_depth_entry,
)
from .workspace import active_workspace_id


_CELL_CACHE_MAX = 2
_CELL_CACHE_LOCK = threading.RLock()
_CELL_CACHE: OrderedDict[tuple[Any, ...], list[dict[str, Any]]] = OrderedDict()


def query_forecast_runs(filters: dict[str, Any] | None = None,
                        limit: int | None = None,
                        order_by: str | None = None,
                        offset: int | None = None) -> list[dict]:
    if not active_workspace_id():
        return []
    rows = read_forecast_runs()
    normalized_filters = normalize_forecast_filters(filters)
    if is_latest_forecast_id((filters or {}).get("forecast_id")):
        rows = rows[-1:]
        normalized_filters.pop("forecast_id", None)
    rows = apply_filters(rows, normalized_filters)
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def query_forecast_cells(filters: dict[str, Any] | None = None,
                         limit: int | None = None,
                         order_by: str | None = None,
                         offset: int | None = None) -> list[dict]:
    if not active_workspace_id():
        return []
    normalized_filters = normalize_forecast_filters(filters)
    rows = cached_forecast_cells(normalized_filters)
    object_filters = {
        key: value for key, value in normalized_filters.items()
        if key not in {"forecast_id", "time_h"}
    }
    rows = apply_filters(rows, object_filters)
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def count_forecast_runs(filters: dict[str, Any] | None = None) -> int:
    return len(query_forecast_runs(filters))


def count_forecast_cells(filters: dict[str, Any] | None = None) -> int:
    return len(query_forecast_cells(filters))


def read_hydrodynamic_depth_csv(path: Path) -> dict[int, float]:
    if not path.exists():
        return {}
    depths: dict[int, float] = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            try:
                cell_id = int(row["cell_id"])
                depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if depth > 0:
                depths[cell_id] = depth
    return depths


def forecast_cells_from_hydrodynamic_mesh(
    depths: dict[int, float],
    generated_at: str,
    time_h: float | None = None,
    forecast_id: str = LATEST_FORECAST_ID,
) -> list[dict[str, Any]]:
    if not MESH_DB_PATH.exists() or not depths:
        return []
    cells = []
    with closing(sqlite3.connect(MESH_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for row in mesh_rows_for_depths(conn, depths):
            mesh_cell_id = int(row["cell_id"])
            depth_m = float(depths.get(mesh_cell_id) or 0)
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
            velocity = round(max(0.04, min(2.4, 0.10 + math.sqrt(depth_m) * 0.38)), 3)
            cells.append({
                "forecast_cell_id": f"{forecast_id}_{mesh_cell_id}",
                "forecast_id": forecast_id,
                "model_name": "FLOOD_CNN_V2",
                "mesh_cell_id": str(mesh_cell_id),
                "mesh_source_id": "cnn_v2_gt",
                "time_h": round(float(time_h), 3) if time_h is not None else None,
                "lead_time_h": round(float(time_h), 3) if time_h is not None else None,
                "view": "time_slice" if time_h is not None else "envelope",
                "centroid_lon": round(centroid[0], 7),
                "centroid_lat": round(centroid[1], 7),
                "distance_to_river_m": None,
                "river_along_ratio": None,
                "ground_elevation_m": None,
                "water_level_m": None,
                "depth_m": round(depth_m, 3),
                "depth_source": "cnn_prediction",
                "velocity_mps": velocity,
                "velocity_source": "depth_estimate",
                # A selected slice is not the first arrival, and depth is not
                # an absolute water level. Do not manufacture missing outputs.
                "arrival_time_h": None,
                "recession_time_h": None,
                "risk_level": risk_level(depth_m, velocity),
                "risk_basis": "depth_and_estimated_velocity",
                "area_m2": round(triangle_area_m2(coordinates[:3]), 3),
                "geometry_type": "Polygon",
                "geometry_crs": "EPSG:4326",
                "geometry": json.dumps({"type": "Polygon", "coordinates": [coordinates]}, ensure_ascii=False),
                "generated_at": generated_at,
            })
    return cells


def forecast_cell_summary_from_hydrodynamic_mesh(depths: dict[int, float]) -> dict[str, Any]:
    if not MESH_DB_PATH.exists() or not depths:
        return {"forecast_cell_count": 0, "inundated_area_km2": 0.0}
    count = 0
    total_area_m2 = 0.0
    with sqlite3.connect(MESH_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for row in mesh_rows_for_depths(conn, depths):
            count += 1
            total_area_m2 += triangle_area_m2([
                [float(row["lon1"]), float(row["lat1"])],
                [float(row["lon2"]), float(row["lat2"])],
                [float(row["lon3"]), float(row["lat3"])],
            ])
    return {
        "forecast_cell_count": count,
        "inundated_area_km2": round(total_area_m2 / 1_000_000, 4),
    }


def mesh_rows_for_depths(conn: sqlite3.Connection,
                         depths: dict[int, float]) -> list[sqlite3.Row]:
    cell_ids = sorted(int(cell_id) for cell_id, depth in depths.items() if float(depth or 0) >= 0.04)
    rows: list[sqlite3.Row] = []
    for start in range(0, len(cell_ids), 800):
        batch = cell_ids[start:start + 800]
        placeholders = ",".join("?" for _ in batch)
        rows.extend(conn.execute(
            f"select cell_id, lon1, lat1, lon2, lat2, lon3, lat3 from cells where cell_id in ({placeholders}) order by cell_id",
            batch,
        ).fetchall())
    return rows


def cached_forecast_cells(filters: dict[str, Any]) -> list[dict[str, Any]]:
    forecast_id = str(filters.get("forecast_id") or LATEST_FORECAST_ID)
    resolved_forecast_id = resolve_forecast_id(forecast_id)
    requested_time_h = coerce_optional_float(filters.get("time_h"))
    depth_entry = forecast_depth_entry(forecast_id, time_h=requested_time_h)
    cache_key = (active_workspace_id(), forecast_id, depth_entry.get("time_h"), depth_entry.get("stat_key"))
    with _CELL_CACHE_LOCK:
        cached = _CELL_CACHE.get(cache_key)
        if cached is not None:
            _CELL_CACHE.move_to_end(cache_key)
            return cached
        rows = forecast_cells_from_hydrodynamic_mesh(
            depth_entry["depths"],
            generated_at=forecast_generated_at(resolved_forecast_id),
            time_h=depth_entry.get("time_h"),
            forecast_id=resolved_forecast_id,
        )
        _CELL_CACHE[cache_key] = rows
        _CELL_CACHE.move_to_end(cache_key)
        while len(_CELL_CACHE) > _CELL_CACHE_MAX:
            _CELL_CACHE.popitem(last=False)
        return rows


def clear_forecast_cell_cache() -> None:
    with _CELL_CACHE_LOCK:
        _CELL_CACHE.clear()


def forecast_generated_at(forecast_id: str = LATEST_FORECAST_ID) -> str:
    rows = read_forecast_runs()
    if rows:
        if forecast_id not in {"latest", LATEST_FORECAST_ID}:
            selected = next((row for row in rows if row.get("forecast_id") == forecast_id), None)
            if selected:
                return str(selected.get("generated_at") or "")
        return str(rows[-1].get("generated_at") or "")
    return datetime.now(timezone.utc).isoformat()


def risk_level(depth_m: float, velocity_mps: float) -> str:
    if depth_m >= 1.6 or depth_m * velocity_mps >= 1.2:
        return "critical"
    if depth_m >= 0.9 or depth_m * velocity_mps >= 0.55:
        return "high"
    if depth_m >= 0.35:
        return "medium"
    return "low"


def normalize_forecast_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(filters or {})
    if is_latest_forecast_id(normalized.get("forecast_id")):
        normalized["forecast_id"] = LATEST_FORECAST_ID
    return normalized


def is_latest_forecast_id(value: Any) -> bool:
    return str(value or "") in {"latest", LATEST_FORECAST_ID}


def resolve_forecast_id(value: Any) -> str:
    forecast_id = str(value or LATEST_FORECAST_ID)
    if not is_latest_forecast_id(forecast_id):
        return forecast_id
    rows = read_forecast_runs()
    return str(rows[-1].get("forecast_id") or LATEST_FORECAST_ID) if rows else LATEST_FORECAST_ID


__all__ = [name for name in globals() if not name.startswith("_")]
