from __future__ import annotations

import csv
import json
import math
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from .common import DOMAIN_DATA_DIR, DOMAIN_DIR, PROJECT_DIR, apply_filters, apply_order, apply_window
from .coordinates import gcj02_to_wgs84
from .workspace import workspace_dir


MODEL_DIR = DOMAIN_DIR / "model" / "cnn_v2"
GT_PATH = MODEL_DIR / "GT.txt"
HYDRODYNAMIC_DATA_DIR = DOMAIN_DATA_DIR / "hydrodynamic"
MESH_DB_PATH = HYDRODYNAMIC_DATA_DIR / "mesh.sqlite"
MIN_TILE_ZOOM = 13
SUPPORTED_TILE_ZOOMS = (13, 14, 15)
LATEST_FORECAST_ID = "latest"
MESH_ONLY_ID = "mesh"
_DEPTH_CACHE_LOCK = threading.Lock()
_DEPTH_CACHE_MAX = 8
_DEPTH_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_TILE_CACHE_LOCK = threading.Lock()
_TILE_CACHE_MAX = 1024
_TILE_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


class HydrodynamicMeshStore:
    def __init__(self, db_path: Path = MESH_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()

    def ensure_ready(self) -> None:
        if self._is_ready():
            return
        with self._lock:
            if self._is_ready():
                return
            self._build()

    def meta(self, forecast_id: str = LATEST_FORECAST_ID) -> dict[str, Any]:
        self.ensure_ready()
        with self._connect() as conn:
            mesh = {row["key"]: row["value"] for row in conn.execute("select key, value from mesh_meta")}
            forecast = forecast_stats(forecast_id)
            return {
                "object_type": "HydrodynamicCell",
                "label": "水动力模型网格",
                "feature_count": int(mesh.get("feature_count", 0)),
                "geometry_type": "Polygon",
                "source_crs": mesh.get("source_crs", "EPSG:4546"),
                "map_crs": mesh.get("map_crs", "EPSG:4326"),
                "min_tile_zoom": MIN_TILE_ZOOM,
                "supported_tile_zooms": list(SUPPORTED_TILE_ZOOMS),
                "bbox": {
                    "min_lon": float(mesh.get("min_lon", 0)),
                    "min_lat": float(mesh.get("min_lat", 0)),
                    "max_lon": float(mesh.get("max_lon", 0)),
                    "max_lat": float(mesh.get("max_lat", 0)),
                },
                "mesh_path": str(self.db_path.relative_to(PROJECT_DIR)),
                "source_paths": {
                    "grid": str(GT_PATH.relative_to(PROJECT_DIR)) if GT_PATH.exists() else "",
                },
                "forecast": forecast,
            }

    def tile(self, z: int, x: int, y: int,
             forecast_id: str = LATEST_FORECAST_ID,
             wet_only: bool = False,
             time_h: float | None = None,
             tile_crs: str = "wgs84") -> dict[str, Any]:
        self.ensure_ready()
        if z < MIN_TILE_ZOOM:
            return {
                "cells": [],
                "too_coarse": True,
                "min_tile_zoom": MIN_TILE_ZOOM,
            }
        forecast_id = normalize_forecast_id(forecast_id)
        tile_crs = normalize_tile_crs(tile_crs)
        depth_entry = forecast_depth_entry(forecast_id, time_h=time_h)
        depths = depth_entry["depths"]
        cache_key = (
            z, x, y, tile_crs, forecast_id, depth_entry.get("time_h"),
            bool(wet_only), depth_entry["stat_key"],
        )
        with _TILE_CACHE_LOCK:
            cached = _TILE_CACHE.get(cache_key)
            if cached:
                _TILE_CACHE.move_to_end(cache_key)
                return cached

        rows = self._tile_rows(z, x, y, tile_crs)

        cells = []
        for row in rows:
            depth = depths.get(int(row["cell_id"]), 0.0)
            if wet_only and depth <= 0:
                continue
            cells.append([
                row["cell_id"],
                round(depth, 4),
                round(row["lon1"], 7),
                round(row["lat1"], 7),
                round(row["lon2"], 7),
                round(row["lat2"], 7),
                round(row["lon3"], 7),
                round(row["lat3"], 7),
            ])
        result = {
            "cells": cells,
            "count": len(cells),
            "forecast_id": forecast_id,
            "time_h": depth_entry.get("time_h"),
            "time_index": depth_entry.get("time_index"),
            "z": z,
            "x": x,
            "y": y,
            "tile_crs": tile_crs,
        }
        with _TILE_CACHE_LOCK:
            _TILE_CACHE[cache_key] = result
            _TILE_CACHE.move_to_end(cache_key)
            while len(_TILE_CACHE) > _TILE_CACHE_MAX:
                _TILE_CACHE.popitem(last=False)
        return result

    def _tile_rows(self, z: int, x: int, y: int,
                   tile_crs: str = "wgs84") -> list[sqlite3.Row]:
        with self._connect() as conn:
            if tile_crs == "gcj02":
                return self._bbox_rows(conn, z, gcj02_tile_bounds_wgs84(z, x, y))
            if z in SUPPORTED_TILE_ZOOMS:
                return conn.execute(
                    """
                    select c.cell_id, c.lon1, c.lat1, c.lon2, c.lat2, c.lon3, c.lat3
                    from tile_cells tc
                    join cells c on c.cell_id = tc.cell_id
                    where tc.z = ? and tc.x = ? and tc.y = ?
                    order by c.cell_id
                    """,
                    (z, x, y),
                ).fetchall()

            return self._bbox_rows(conn, z, tile_bounds(z, x, y))

    def _bbox_rows(self, conn: sqlite3.Connection, z: int,
                   bounds: tuple[float, float, float, float]) -> list[sqlite3.Row]:
        min_lon, min_lat, max_lon, max_lat = bounds
        if z in SUPPORTED_TILE_ZOOMS:
            min_x, max_y = lonlat_to_tile(min_lon, min_lat, z)
            max_x, min_y = lonlat_to_tile(max_lon, max_lat, z)
            return conn.execute(
                """
                select distinct c.cell_id, c.lon1, c.lat1, c.lon2, c.lat2, c.lon3, c.lat3
                from tile_cells tc
                join cells c on c.cell_id = tc.cell_id
                where tc.z = ?
                  and tc.x between ? and ?
                  and tc.y between ? and ?
                  and c.max_lon >= ?
                  and c.min_lon <= ?
                  and c.max_lat >= ?
                  and c.min_lat <= ?
                order by c.cell_id
                """,
                (
                    z, min(min_x, max_x), max(min_x, max_x),
                    min(min_y, max_y), max(min_y, max_y),
                    min_lon, max_lon, min_lat, max_lat,
                ),
            ).fetchall()
        return conn.execute(
                """
                select cell_id, lon1, lat1, lon2, lat2, lon3, lat3
                from cells
                where max_lon >= ?
                  and min_lon <= ?
                  and max_lat >= ?
                  and min_lat <= ?
                order by cell_id
                """,
                (min_lon, max_lon, min_lat, max_lat),
        ).fetchall()

    def query(self, filters: dict[str, Any] | None = None,
              limit: int | None = None, order_by: str | None = None,
              offset: int | None = None) -> list[dict[str, Any]]:
        self.ensure_ready()
        forecast_id = str((filters or {}).get("forecast_id") or LATEST_FORECAST_ID)
        time_h = coerce_optional_float((filters or {}).get("time_h"))
        depths = read_forecast_depths(forecast_id, time_h=time_h)
        with self._connect() as conn:
            rows = [
                {
                    "hydrodynamic_cell_id": f"hydro_cell_{row['cell_id']}",
                    "cell_id": row["cell_id"],
                    "forecast_id": normalize_forecast_id(forecast_id),
                    "time_h": time_h,
                    "depth_m": depths.get(int(row["cell_id"]), 0.0),
                    "is_flooded": depths.get(int(row["cell_id"]), 0.0) > 0,
                    "geometry_type": "Polygon",
                    "geometry_crs": "EPSG:4326",
                }
                for row in conn.execute("select cell_id from cells order by cell_id")
            ]
        object_filters = {
            key: value for key, value in (filters or {}).items()
            if key != "forecast_id"
        }
        rows = apply_filters(rows, object_filters)
        rows = apply_order(rows, order_by)
        return apply_window(rows, limit, offset)

    def count(self, filters: dict[str, Any] | None = None) -> int:
        if filters:
            return len(self.query(filters))
        self.ensure_ready()
        with self._connect() as conn:
            return int(conn.execute("select count(*) from cells").fetchone()[0])

    def _is_ready(self) -> bool:
        if not self.db_path.exists():
            return False
        try:
            with self._connect() as conn:
                version = conn.execute(
                    "select value from mesh_meta where key = 'schema_version'",
                ).fetchone()
                return bool(version and version["value"] == "1")
        except sqlite3.Error:
            return False

    def _build(self) -> None:
        if not GT_PATH.exists():
            raise FileNotFoundError(f"hydrodynamic grid not found: {GT_PATH}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.db_path.with_suffix(".sqlite.tmp")
        if temp_path.exists():
            temp_path.unlink()

        with sqlite3.connect(temp_path) as conn:
            conn.execute("pragma journal_mode = off")
            conn.execute("pragma synchronous = off")
            conn.execute(
                """
                create table cells(
                    cell_id integer primary key,
                    min_lon real not null,
                    min_lat real not null,
                    max_lon real not null,
                    max_lat real not null,
                    lon1 real not null,
                    lat1 real not null,
                    lon2 real not null,
                    lat2 real not null,
                    lon3 real not null,
                    lat3 real not null
                )
                """
            )
            conn.execute(
                """
                create table tile_cells(
                    z integer not null,
                    x integer not null,
                    y integer not null,
                    cell_id integer not null,
                    primary key(z, x, y, cell_id)
                )
                """
            )
            conn.execute("create table mesh_meta(key text primary key, value text not null)")

            cells, meta = parse_gt_cells()
            conn.executemany(
                """
                insert into cells(
                    cell_id, min_lon, min_lat, max_lon, max_lat,
                    lon1, lat1, lon2, lat2, lon3, lat3
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cells,
            )
            conn.executemany(
                "insert into tile_cells(z, x, y, cell_id) values (?, ?, ?, ?)",
                tile_index_rows(cells),
            )
            meta.update({
                "schema_version": "1",
                "source_crs": "EPSG:4546",
                "map_crs": "EPSG:4326",
                "source_grid": str(GT_PATH.relative_to(PROJECT_DIR)),
            })
            conn.executemany(
                "insert into mesh_meta(key, value) values (?, ?)",
                [(key, str(value)) for key, value in sorted(meta.items())],
            )
            conn.execute("create index idx_tile_cells on tile_cells(z, x, y)")
            conn.execute("create index idx_cells_bbox on cells(min_lon, min_lat, max_lon, max_lat)")

        temp_path.replace(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


STORE = HydrodynamicMeshStore()


def hydrodynamic_grid_stats(forecast_id: str = LATEST_FORECAST_ID) -> dict[str, Any]:
    return STORE.meta(forecast_id)


def hydrodynamic_grid_tile(z: int, x: int, y: int,
                           forecast_id: str = LATEST_FORECAST_ID,
                           wet_only: bool = False,
                           time_h: float | None = None,
                           tile_crs: str = "wgs84") -> dict[str, Any]:
    return STORE.tile(z, x, y, forecast_id, wet_only, time_h, tile_crs)


def query_hydrodynamic_cells(filters: dict[str, Any] | None = None,
                             limit: int | None = None,
                             order_by: str | None = None,
                             offset: int | None = None) -> list[dict[str, Any]]:
    return STORE.query(filters, limit, order_by, offset)


def count_hydrodynamic_cells(filters: dict[str, Any] | None = None) -> int:
    return STORE.count(filters)


def parse_gt_cells() -> tuple[list[tuple], dict[str, Any]]:
    with GT_PATH.open(encoding="utf-8", errors="ignore") as file:
        header = file.readline().split()
        node_count = int(header[0])
        cell_count = int(header[1])
        lons = [0.0] * node_count
        lats = [0.0] * node_count

        min_lon = min_lat = float("inf")
        max_lon = max_lat = float("-inf")
        for _ in range(node_count):
            node_id_text, x_text, y_text = file.readline().split()[:3]
            node_id = int(node_id_text)
            lon, lat = epsg4546_to_wgs84(float(x_text), float(y_text))
            lons[node_id] = lon
            lats[node_id] = lat
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)

        cells = []
        for _ in range(cell_count):
            parts = file.readline().split()
            if len(parts) < 4:
                continue
            cell_id = int(parts[0])
            n1, n2, n3 = int(parts[1]), int(parts[2]), int(parts[3])
            lon1, lat1 = lons[n1], lats[n1]
            lon2, lat2 = lons[n2], lats[n2]
            lon3, lat3 = lons[n3], lats[n3]
            cells.append((
                cell_id,
                min(lon1, lon2, lon3),
                min(lat1, lat2, lat3),
                max(lon1, lon2, lon3),
                max(lat1, lat2, lat3),
                lon1,
                lat1,
                lon2,
                lat2,
                lon3,
                lat3,
            ))

    return cells, {
        "feature_count": len(cells),
        "node_count": node_count,
        "source_cell_count": cell_count,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }


def tile_index_rows(cells: list[tuple]) -> list[tuple[int, int, int, int]]:
    rows: list[tuple[int, int, int, int]] = []
    for cell in cells:
        cell_id = int(cell[0])
        min_lon, min_lat, max_lon, max_lat = cell[1:5]
        for z in SUPPORTED_TILE_ZOOMS:
            min_x, max_y = lonlat_to_tile(min_lon, min_lat, z)
            max_x, min_y = lonlat_to_tile(max_lon, max_lat, z)
            for x in range(min(min_x, max_x), max(min_x, max_x) + 1):
                for y in range(min(min_y, max_y), max(min_y, max_y) + 1):
                    rows.append((z, x, y, cell_id))
    return rows


def forecast_stats(forecast_id: str = LATEST_FORECAST_ID) -> dict[str, Any]:
    if normalize_forecast_id(forecast_id) == MESH_ONLY_ID:
        return {
            "forecast_id": MESH_ONLY_ID,
            "depth_path": "",
            "depth_count": 0,
            "flooded_count": 0,
            "max_depth_m": 0.0,
        }
    path = forecast_depth_path(forecast_id)
    entry = forecast_depth_entry(forecast_id)
    series_path = forecast_series_path(forecast_id)
    time_steps = forecast_time_steps(forecast_id)
    return {
        "forecast_id": normalize_forecast_id(forecast_id),
        "depth_path": str(path.relative_to(PROJECT_DIR)) if path.exists() else "",
        "series_path": str(series_path.relative_to(PROJECT_DIR)) if series_path.exists() else "",
        "depth_count": entry["depth_count"],
        "flooded_count": entry["flooded_count"],
        "max_depth_m": round(entry["max_depth_m"], 4),
        "time_steps_h": time_steps,
        "time_step_count": len(time_steps),
    }


def read_forecast_depths(forecast_id: str = LATEST_FORECAST_ID,
                         time_h: float | None = None) -> dict[int, float]:
    return forecast_depth_entry(forecast_id, time_h=time_h)["depths"]


def forecast_depth_entry(forecast_id: str = LATEST_FORECAST_ID,
                         time_h: float | None = None) -> dict[str, Any]:
    if normalize_forecast_id(forecast_id) == MESH_ONLY_ID:
        return {
            "stat_key": None,
            "depths": {},
            "depth_count": 0,
            "flooded_count": 0,
            "max_depth_m": 0.0,
            "time_h": None,
            "time_index": None,
        }
    if time_h is not None:
        return forecast_time_depth_entry(forecast_id, float(time_h))
    path = forecast_depth_path(forecast_id)
    stat_key = file_stat_key(path)
    cache_key = ("max", normalize_forecast_id(forecast_id), str(path.resolve()), stat_key)
    with _DEPTH_CACHE_LOCK:
        cached = _DEPTH_CACHE.get(cache_key)
        if cached and cached.get("stat_key") == stat_key:
            _DEPTH_CACHE.move_to_end(cache_key)
            return cached

    entry = load_forecast_depth_entry(path, stat_key)

    with _DEPTH_CACHE_LOCK:
        cached = _DEPTH_CACHE.get(cache_key)
        if cached and cached.get("stat_key") == stat_key:
            _DEPTH_CACHE.move_to_end(cache_key)
            return cached
        return cache_depth_entry(cache_key, entry)


def forecast_time_depth_entry(forecast_id: str, time_h: float) -> dict[str, Any]:
    series_path = forecast_series_path(forecast_id)
    steps = forecast_time_steps(forecast_id)
    stat_key = (file_stat_key(series_path), file_stat_key(forecast_time_steps_path(forecast_id)), nearest_time_key(steps, time_h))
    cache_key = ("time", normalize_forecast_id(forecast_id), str(series_path.resolve()), stat_key)
    with _DEPTH_CACHE_LOCK:
        cached = _DEPTH_CACHE.get(cache_key)
        if cached and cached.get("stat_key") == stat_key:
            _DEPTH_CACHE.move_to_end(cache_key)
            return cached

    entry = load_forecast_time_depth_entry(series_path, steps, time_h, stat_key)

    with _DEPTH_CACHE_LOCK:
        cached = _DEPTH_CACHE.get(cache_key)
        if cached and cached.get("stat_key") == stat_key:
            _DEPTH_CACHE.move_to_end(cache_key)
            return cached
        return cache_depth_entry(cache_key, entry)


def cache_depth_entry(cache_key: tuple[Any, ...], entry: dict[str, Any]) -> dict[str, Any]:
    _DEPTH_CACHE[cache_key] = entry
    _DEPTH_CACHE.move_to_end(cache_key)
    while len(_DEPTH_CACHE) > _DEPTH_CACHE_MAX:
        _DEPTH_CACHE.popitem(last=False)
    return entry


def file_stat_key(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def load_forecast_depth_entry(path: Path, stat_key: tuple[int, int] | None) -> dict[str, Any]:
    if not path.exists():
        return {
            "stat_key": stat_key,
            "depths": {},
            "depth_count": 0,
            "flooded_count": 0,
            "max_depth_m": 0.0,
        }
    depths = {}
    flooded_count = 0
    max_depth = 0.0
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            depths[int(row["cell_id"])] = depth
            if depth > 0:
                flooded_count += 1
                max_depth = max(max_depth, depth)
    return {
        "stat_key": stat_key,
        "depths": depths,
        "depth_count": len(depths),
        "flooded_count": flooded_count,
        "max_depth_m": max_depth,
        "time_h": None,
        "time_index": None,
    }


def load_forecast_time_depth_entry(path: Path, steps: list[float],
                                   requested_time_h: float,
                                   stat_key: tuple[Any, ...]) -> dict[str, Any]:
    if not path.exists() or not steps:
        return {
            "stat_key": stat_key,
            "depths": {},
            "depth_count": 0,
            "flooded_count": 0,
            "max_depth_m": 0.0,
            "time_h": None,
            "time_index": None,
        }
    array = np.load(path, mmap_mode="r")
    index = nearest_time_index(steps, requested_time_h)
    if index >= int(array.shape[0]):
        index = int(array.shape[0]) - 1
    values = np.asarray(array[index], dtype=np.float32)
    wet_indices = np.flatnonzero(values > 0)
    depths = {int(item) + 1: float(values[item]) for item in wet_indices}
    return {
        "stat_key": stat_key,
        "depths": depths,
        "depth_count": int(values.shape[0]),
        "flooded_count": int(wet_indices.size),
        "max_depth_m": float(values.max()) if values.size else 0.0,
        "time_h": steps[index] if index < len(steps) else None,
        "time_index": index,
    }


def forecast_depth_path(forecast_id: str = LATEST_FORECAST_ID) -> Path:
    forecast_id = normalize_forecast_id(forecast_id)
    if forecast_id == MESH_ONLY_ID:
        return Path("")
    if forecast_id == LATEST_FORECAST_ID:
        return workspace_dir() / "forecasts" / "latest" / "max_depth.csv"
    return HYDRODYNAMIC_DATA_DIR / "forecasts" / forecast_id / "max_depth.csv"


def forecast_series_path(forecast_id: str = LATEST_FORECAST_ID) -> Path:
    forecast_id = normalize_forecast_id(forecast_id)
    if forecast_id == MESH_ONLY_ID:
        return Path("")
    if forecast_id == LATEST_FORECAST_ID:
        return workspace_dir() / "forecasts" / "latest" / "depth_series.npy"
    return HYDRODYNAMIC_DATA_DIR / "forecasts" / forecast_id / "depth_series.npy"


def forecast_time_steps_path(forecast_id: str = LATEST_FORECAST_ID) -> Path:
    forecast_id = normalize_forecast_id(forecast_id)
    if forecast_id == MESH_ONLY_ID:
        return Path("")
    if forecast_id == LATEST_FORECAST_ID:
        return workspace_dir() / "forecasts" / "latest" / "time_steps.json"
    return HYDRODYNAMIC_DATA_DIR / "forecasts" / forecast_id / "time_steps.json"


def forecast_time_steps(forecast_id: str = LATEST_FORECAST_ID) -> list[float]:
    path = forecast_time_steps_path(forecast_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [float(value) for value in data.get("time_steps_h") or []]


def nearest_time_index(steps: list[float], time_h: float) -> int:
    if not steps:
        return 0
    return min(range(len(steps)), key=lambda index: abs(float(steps[index]) - time_h))


def nearest_time_key(steps: list[float], time_h: float) -> float | None:
    if not steps:
        return None
    return steps[nearest_time_index(steps, time_h)]


def coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_forecast_id(forecast_id: str = LATEST_FORECAST_ID) -> str:
    value = str(forecast_id or LATEST_FORECAST_ID)
    if value in {"mesh", "none", "static"}:
        return MESH_ONLY_ID
    return LATEST_FORECAST_ID if value in {"latest", "forecast_latest"} else value


def normalize_tile_crs(tile_crs: str = "wgs84") -> str:
    value = str(tile_crs or "wgs84").strip().lower()
    return "gcj02" if value in {"gcj02", "gcj-02", "amap", "gaode"} else "wgs84"


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2 ** z
    min_lon = x / n * 360.0 - 180.0
    max_lon = (x + 1) / n * 360.0 - 180.0
    max_lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    min_lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return min_lon, min_lat, max_lon, max_lat


def gcj02_tile_bounds_wgs84(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    min_lng, min_lat, max_lng, max_lat = tile_bounds(z, x, y)
    gcj_points = [
        (min_lng, min_lat),
        (min_lng, max_lat),
        (max_lng, min_lat),
        (max_lng, max_lat),
        ((min_lng + max_lng) / 2, min_lat),
        ((min_lng + max_lng) / 2, max_lat),
        (min_lng, (min_lat + max_lat) / 2),
        (max_lng, (min_lat + max_lat) / 2),
    ]
    wgs_points = [gcj02_to_wgs84(lng, lat) for lng, lat in gcj_points]
    padding = 1e-7
    return (
        min(point[0] for point in wgs_points) - padding,
        min(point[1] for point in wgs_points) - padding,
        max(point[0] for point in wgs_points) + padding,
        max(point[1] for point in wgs_points) + padding,
    )


def epsg4546_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    # EPSG:4546 is CGCS2000 / 3-degree Gauss-Kruger CM 111E. CGCS2000 is close
    # enough to WGS84 for web map display at this scale after the projection inverse.
    a = 6378137.0
    inv_f = 298.257222101
    f = 1 / inv_f
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    lon0 = math.radians(111.0)
    x = easting - 500000.0
    m = northing
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    sin_phi = math.sin(phi1)
    cos_phi = math.cos(phi1)
    tan_phi = math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * sin_phi ** 2)
    r1 = a * (1 - e2) / (1 - e2 * sin_phi ** 2) ** 1.5
    t1 = tan_phi ** 2
    c1 = ep2 * cos_phi ** 2
    d = x / n1
    lat = phi1 - (n1 * tan_phi / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos_phi
    return math.degrees(lon), math.degrees(lat)
