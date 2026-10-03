"""Geometry and spatial indexing helpers for forecast cells and objects."""

from __future__ import annotations

import json
import math
from typing import Any


def triangle_area_m2(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0.0
    ref_lat = sum(float(point[1]) for point in points[:3]) / 3
    projected = [project((point[0], point[1]), ref_lat) for point in points[:3]]
    return abs(sum(
        projected[index][0] * projected[(index + 1) % 3][1]
        - projected[(index + 1) % 3][0] * projected[index][1]
        for index in range(3)
    ) / 2)


def nearest_cell(point: tuple[float, float], cells: dict[str, Any],
                 max_distance_m: float) -> dict[str, Any] | None:
    best = None
    best_distance = max_distance_m
    for cell in candidate_cells(point, cells, max_distance_m):
        distance = distance_m(point, (
            float(cell["centroid_lon"]), float(cell["centroid_lat"]),
        ))
        if distance <= best_distance:
            best = cell
            best_distance = distance
    if best is None:
        return None
    return {**best, "_distance_m": best_distance}


def nearby_cells(point: tuple[float, float], cells: dict[str, Any],
                 max_distance_m: float) -> list[dict[str, Any]]:
    matched = []
    for cell in candidate_cells(point, cells, max_distance_m):
        distance = cell_geometry_distance_m(point, cell)
        if distance <= max_distance_m:
            matched.append({**cell, "_distance_m": distance})
    return sorted(matched, key=lambda row: float(row["_distance_m"]))


def cell_geometry_distance_m(point: tuple[float, float],
                             cell: dict[str, Any]) -> float:
    geometry = cell.get("geometry") or {}
    if isinstance(geometry, str):
        try:
            geometry = json.loads(geometry)
        except json.JSONDecodeError:
            geometry = {}
    if isinstance(geometry, dict):
        rings = polygon_rings(geometry)
        if any(point_in_ring(point, ring) for ring in rings):
            return 0.0
        distances = [
            point_segment_distance_m(point, start, end)[0]
            for ring in rings
            for start, end in closed_segments(ring)
        ]
        if distances:
            return min(distances)
    return distance_m(point, (
        float(cell["centroid_lon"]), float(cell["centroid_lat"]),
    ))


def polygon_rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        values = coordinates
    elif geometry_type == "MultiPolygon":
        values = [ring for polygon in coordinates for ring in polygon]
    else:
        return []
    return [
        [(float(point[0]), float(point[1])) for point in ring]
        for ring in values
        if isinstance(ring, list) and len(ring) >= 3
    ]


def point_in_ring(point: tuple[float, float],
                  ring: list[tuple[float, float]]) -> bool:
    inside = False
    for start, end in closed_segments(ring):
        if ((start[1] > point[1]) != (end[1] > point[1]) and
                point[0] < (end[0] - start[0]) * (point[1] - start[1]) /
                (end[1] - start[1] or 1e-15) + start[0]):
            inside = not inside
    return inside


def closed_segments(ring: list[tuple[float, float]]):
    if len(ring) < 2:
        return []
    points = ring if ring[0] == ring[-1] else [*ring, ring[0]]
    return list(zip(points, points[1:]))


def build_cell_spatial_index(cells: list[dict[str, Any]]) -> dict[str, Any]:
    degree_size = 0.002
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for cell in cells:
        key = cell_bucket((
            float(cell["centroid_lon"]), float(cell["centroid_lat"]),
        ), degree_size)
        buckets.setdefault(key, []).append(cell)
    return {"degree_size": degree_size, "buckets": buckets, "cells": cells}


def candidate_cells(point: tuple[float, float], cells: dict[str, Any],
                    max_distance_m: float) -> list[dict[str, Any]]:
    if not isinstance(cells, dict) or "buckets" not in cells:
        return cells
    degree_size = float(cells.get("degree_size") or 0.002)
    center = cell_bucket(point, degree_size)
    span = max(1, math.ceil(max_distance_m / (degree_size * 90_000.0)) + 1)
    result = []
    buckets = cells.get("buckets") or {}
    for x in range(center[0] - span, center[0] + span + 1):
        for y in range(center[1] - span, center[1] + span + 1):
            result.extend(buckets.get((x, y), []))
    return result


def cell_bucket(point: tuple[float, float], degree_size: float) -> tuple[int, int]:
    return math.floor(point[0] / degree_size), math.floor(point[1] / degree_size)


def row_point(row: dict[str, Any]) -> tuple[float, float] | None:
    lon = row.get("longitude")
    lat = row.get("latitude")
    if lon and lat:
        return float(lon), float(lat)
    geometry = json.loads(row.get("geometry") or "{}")
    return geometry_centroid(geometry)


def sampled_geometry_points(row: dict[str, Any], max_points: int = 16) -> list[tuple[float, float]]:
    geometry = json.loads(row.get("geometry") or "{}")
    coords = iter_coords(geometry.get("coordinates") or [])
    if len(coords) <= max_points:
        return coords
    step = max(1, len(coords) // max_points)
    return coords[::step][:max_points]


def geometry_centroid(geometry: dict) -> tuple[float, float] | None:
    coords = list(iter_coords(geometry.get("coordinates") or []))
    if not coords:
        return None
    return (
        sum(lon for lon, _ in coords) / len(coords),
        sum(lat for _, lat in coords) / len(coords),
    )


def iter_coords(value) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        return [(float(value[0]), float(value[1]))]
    coords: list[tuple[float, float]] = []
    for item in value:
        coords.extend(iter_coords(item))
    return coords


def point_segment_distance_m(point: tuple[float, float],
                             start: tuple[float, float],
                             end: tuple[float, float]) -> tuple[float, float]:
    px, py = project(point, point[1])
    ax, ay = project(start, point[1])
    bx, by = project(end, point[1])
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), 0.0
    ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    closest_x = ax + ratio * dx
    closest_y = ay + ratio * dy
    return math.hypot(px - closest_x, py - closest_y), ratio


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = project(a, (a[1] + b[1]) / 2)
    bx, by = project(b, (a[1] + b[1]) / 2)
    return math.hypot(ax - bx, ay - by)


def project(point: tuple[float, float], ref_lat: float) -> tuple[float, float]:
    return (
        point[0] * 111_320.0 * math.cos(math.radians(ref_lat)),
        point[1] * 110_540.0,
    )


__all__ = [name for name in globals() if not name.startswith("_")]
