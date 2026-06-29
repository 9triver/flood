from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .common import (
    PROJECT_DIR,
    apply_filters,
    apply_order,
    apply_window,
    as_float,
    code,
    first_non_empty,
    rel,
)


def query_cells(resolver, filters: dict[str, Any] | None = None,
                limit: int | None = None, order_by: str | None = None,
                offset: int | None = None) -> list[dict]:
    scenario = find_scenario(
        resolver,
        (filters or {}).get("scenario_id", ""),
        int((filters or {}).get("return_period_year", 0) or 0),
    )
    scenarios = [scenario] if scenario else resolver.scenarios[:1]
    rows = []
    for item in scenarios:
        source = scenario_path(item)
        for i, feature in enumerate(features(source), 1):
            rows.append(cell_record(item["scenario_id"], i, feature, source))
    rows = apply_filters(rows, filters)
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def count_cells(resolver, filters: dict[str, Any] | None = None) -> int:
    if not filters or set(filters) <= {"scenario_id", "return_period_year"}:
        scenario = find_scenario(
            resolver,
            (filters or {}).get("scenario_id", ""),
            int((filters or {}).get("return_period_year", 0) or 0),
        )
        scenarios = [scenario] if scenario else resolver.scenarios
        return sum(ogr_metadata(scenario_path(item)).get("feature_count", 0) for item in scenarios if item)
    return len(resolver.query("Cell", filters))


def find_scenario(resolver, scenario_id: str = "",
                  return_period_year: int = 0) -> dict | None:
    if scenario_id:
        return next((row for row in resolver.scenarios if row["scenario_id"] == scenario_id), None)
    if return_period_year:
        return next((row for row in resolver.scenarios
                     if row.get("return_period_year") == int(return_period_year)), None)
    return resolver.scenarios[0] if resolver.scenarios else None


def scenario_path(scenario: dict) -> Path:
    return PROJECT_DIR / scenario["data_path"]


def ogr_metadata(path: Path) -> dict:
    try:
        raw = subprocess.check_output(
            ["ogrinfo", "-json", "-so", "-al", str(path)],
            text=True,
            stderr=subprocess.STDOUT,
        )
        data = json.loads(raw)
        layer = data.get("layers", [{}])[0]
        geom = (layer.get("geometryFields") or [{}])[0]
        extent = geom.get("extent") or []
        return {
            "geometry_type": geom.get("type", ""),
            "source_crs": epsg_from_geom(geom),
            "extent": extent,
            "feature_count": layer.get("featureCount", 0),
        }
    except Exception as exc:
        return {"error": str(exc)}


def ogr_export(source: Path, target: Path, simplify_tolerance: float = 0):
    cmd = [
        "ogr2ogr",
        "-f", "GeoJSON",
        "-t_srs", "EPSG:4326",
        "-lco", "COORDINATE_PRECISION=7",
    ]
    if simplify_tolerance:
        cmd.extend(["-simplify", str(simplify_tolerance)])
    cmd.extend([str(target), str(source)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def features(path: Path) -> list[dict]:
    metadata = ogr_metadata(path)
    result = subprocess.run(
        ["ogr2ogr", "-f", "GeoJSON", "/vsistdout/", "-t_srs", "EPSG:4326", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    rows = json.loads(result.stdout).get("features", [])
    for row in rows:
        row["_source_crs"] = metadata.get("source_crs", "")
        row["_geometry_crs"] = "EPSG:4326"
    return rows


def cell_record(scenario_id: str, index: int, feature: dict, path: Path) -> dict:
    props = feature.get("properties") or {}
    return {
        "cell_id": f"{scenario_id}_{code(first_non_empty(props, 'OBJECTID', 'FID')) or index}",
        "scenario_id": scenario_id,
        "x": as_float(first_non_empty(props, "X", "x")),
        "y": as_float(first_non_empty(props, "Y", "y")),
        "ground_elevation_m": as_float(first_non_empty(props, "Z", "z")),
        "water_level_m": as_float(first_non_empty(props, "Eta", "ETA")),
        "depth_m": as_float(first_non_empty(props, "YMSS", "depth")),
        "velocity_mps": as_float(first_non_empty(props, "HSLS", "velocity")),
        "arrival_time_h": as_float(first_non_empty(props, "DDSJ", "arrival")),
        "recession_time_h": as_float(first_non_empty(props, "XTSJ", "recession")),
        "area_m2": as_float(first_non_empty(props, "WGMJ", "Shape_Area")),
        **geometry_fields(feature),
        "data_path": rel(path),
    }


def epsg_from_geom(geom: dict) -> str:
    coord = geom.get("coordinateSystem") or {}
    ids = re.findall(r'ID\["EPSG",(\d+)\]', coord.get("wkt", ""))
    return f"EPSG:{ids[-1]}" if ids else ""


def geometry_fields(feature: dict) -> dict:
    geom = feature.get("geometry") or {}
    return {
        "geometry_type": geom.get("type", ""),
        "source_crs": feature.get("_source_crs", ""),
        "geometry_crs": feature.get("_geometry_crs", "EPSG:4326") if geom else "",
        "geometry": json.dumps(geom, ensure_ascii=False) if geom else "",
    }
