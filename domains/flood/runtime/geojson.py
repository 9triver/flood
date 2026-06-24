from __future__ import annotations

import json
from typing import Any

from .cell import find_scenario, ogr_export, scenario_path
from .common import GENERATED_DIR, MAPPABLE_OBJECTS, rel


def export_objects_geojson(resolver, object_type: str,
                           filters: dict[str, Any] | None = None,
                           simplify_tolerance: float = 0,
                           force: bool = False) -> dict:
    filters = filters or {}
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    key = export_key(object_type, filters)
    suffix = f"_s{simplify_tolerance:g}" if simplify_tolerance else ""
    target = GENERATED_DIR / f"{key}{suffix}.geojson"
    if target.exists() and not force:
        return geojson_result(object_type, filters, target, cached=True)

    if object_type == "Cell":
        scenario = find_scenario(resolver, filters.get("scenario_id", ""), int(filters.get("return_period_year", 0) or 0))
        if not scenario:
            return {"error": "scenario not found", "filters": filters}
        source = scenario_path(scenario)
        ogr_export(source, target, simplify_tolerance)
        return geojson_result(object_type, {"scenario_id": scenario["scenario_id"]}, target, cached=False)

    rows = resolver.query(object_type, filters=filters)
    collection = {
        "type": "FeatureCollection",
        "name": object_type,
        "features": [feature_from_row(row) for row in rows if row.get("geometry")],
    }
    target.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    return geojson_result(object_type, filters, target, cached=False)


def geojson_result(object_type: str, filters: dict, target, cached: bool) -> dict:
    spec = MAPPABLE_OBJECTS.get(object_type, {})
    return {
        "object_type": object_type,
        "label": spec.get("label", object_type),
        "filters": filters,
        "path": rel(target),
        "absolute_path": str(target),
        "crs": "EPSG:4326",
        "cached": cached,
        "default_style": spec.get("style", {}),
    }


def feature_from_row(row: dict) -> dict:
    geometry = json.loads(row.get("geometry") or "{}")
    properties = {
        key: value for key, value in row.items()
        if key not in {"geometry", "geometry_type"}
    }
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": geometry,
    }


def export_key(object_type: str, filters: dict[str, Any]) -> str:
    if object_type == "Cell":
        scenario_id = filters.get("scenario_id") or f"{filters.get('return_period_year', '')}a"
        return f"{object_type.lower()}_{scenario_id}".strip("_")
    if not filters:
        return object_type.lower()
    parts = [object_type.lower()]
    for key in sorted(filters):
        value = str(filters[key]).replace("/", "_").replace(" ", "_")
        parts.append(f"{key}_{value}")
    return "__".join(parts)
