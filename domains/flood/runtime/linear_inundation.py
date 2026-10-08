"""Full line/polygon overlay for the Shanhu road and evacuation inventories.

All qualifying wet cells enter an STRtree. EPSG:4546 is the local mesh's
projected CRS; distances and lengths are measured in metres, never degrees.
"""
from __future__ import annotations

import json
from functools import lru_cache

from pyproj import Transformer
from shapely import STRtree, from_geojson, transform, union_all
from shapely.errors import ShapelyError
from shapely.ops import nearest_points


METRIC_CRS = "EPSG:4546"
_TO_METRES = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
_TO_WGS84 = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)


@lru_cache(maxsize=65536)
def _project_geometry(raw: str):
    geometry = from_geojson(raw)
    if geometry.is_empty or not geometry.is_valid:
        return None
    projected = transform(geometry, _TO_METRES.transform, interleaved=False)
    return projected if projected.is_valid and not projected.is_empty else None


def metric_geometry(row: dict, allowed_types: tuple[str, ...]):
    if row.get("geometry_crs", "EPSG:4326") != "EPSG:4326":
        return None
    raw = row.get("geometry")
    if not raw:
        return None
    try:
        geometry = _project_geometry(raw if isinstance(raw, str) else json.dumps(raw))
        return geometry if geometry is not None and geometry.geom_type in allowed_types else None
    except (ShapelyError, ValueError, TypeError):
        return None


class WetCellIndex:
    def __init__(self, cells: list[dict], min_depth_m: float):
        self.rows = []
        self.geometries = []
        self.skipped_cell_ids = []
        for cell in cells:
            if float(cell.get("depth_m") or 0) < min_depth_m:
                continue
            geometry = metric_geometry(cell, ("Polygon", "MultiPolygon"))
            if geometry is None:
                self.skipped_cell_ids.append(cell.get("mesh_cell_id") or cell.get("forecast_cell_id"))
                continue
            self.rows.append(cell)
            self.geometries.append(geometry)
        self.tree = STRtree(self.geometries)

    def match(self, row: dict, max_distance_m: float) -> dict | None:
        line = metric_geometry(row, ("LineString", "MultiLineString"))
        if line is None:
            return None
        candidates = self.tree.query(line, predicate="dwithin", distance=max(0.0, max_distance_m))
        overlaps, nearby = [], []
        for index in candidates:
            polygon = self.geometries[index]
            cell = self.rows[index]
            if line.intersects(polygon):
                overlaps.append((cell, polygon, 0.0))
            else:
                nearby.append((cell, polygon, float(line.distance(polygon))))
        # A deeper neighbouring cell must never become the reported road depth
        # when there is direct overlap with a shallower cell.
        matches = overlaps or nearby
        if not matches:
            return {"status": "no_match"}
        matches.sort(key=lambda item: (-float(item[0]["depth_m"]), str(item[0].get("mesh_cell_id"))))
        cell, polygon, distance = matches[0]
        point = nearest_points(line, polygon)[0]
        lon, lat = _TO_WGS84.transform(point.x, point.y)
        overlap_geometry = union_all([line.intersection(item[1]) for item in overlaps])
        return {
            "status": "forecast_overlap" if overlaps else "nearby_flood",
            "cell": {**cell, "_distance_m": distance},
            "point": (lon, lat),
            "overlap_length_m": round(float(overlap_geometry.length), 3),
            "intersecting_mesh_cell_ids": sorted(str(item[0]["mesh_cell_id"]) for item in overlaps),
            "nearby_mesh_cell_ids": sorted(str(item[0]["mesh_cell_id"]) for item in nearby),
            "nearest_distance_m": round(min(item[2] for item in matches), 3),
            "matching_cell_count": len(matches),
        }
