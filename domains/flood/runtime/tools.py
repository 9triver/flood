from __future__ import annotations

from .cell import find_scenario, ogr_metadata, scenario_path
from .common import MAPPABLE_OBJECTS
from .forecast import ensure_latest_forecast
from .hydrodynamic_grid import hydrodynamic_grid_stats


def list_mappable_objects(resolver, object_type: str = "") -> list[dict]:
    object_types = [object_type] if object_type else list(MAPPABLE_OBJECTS)
    rows = []
    for item in object_types:
        spec = MAPPABLE_OBJECTS.get(item)
        if not spec:
            continue
        if item == "Cell":
            count = sum(ogr_metadata(scenario_path(row)).get("feature_count", 0) for row in resolver.scenarios)
            geometry_type = "Polygon"
        elif item == "ForecastCell":
            ensure_latest_forecast(resolver)
            objects = resolver.query(item)
            count = len(objects)
            geometry_type = "Polygon"
        elif item == "HydrodynamicCell":
            stats = hydrodynamic_grid_stats()
            count = stats.get("feature_count", 0)
            geometry_type = stats.get("geometry_type", "Polygon")
        else:
            objects = resolver.query(item)
            count = len(objects)
            geometry_types = sorted({
                row.get("geometry_type", "") for row in objects
                if row.get("geometry_type")
            })
            geometry_type = ",".join(geometry_types)
        rows.append({
            "object_type": item,
            "label": spec.get("label", item),
            "role": spec.get("role", ""),
            "feature_count": count,
            "geometry_type": geometry_type,
            "map_crs": "EPSG:4326",
            "default_style": spec.get("style", {}),
        })
    return rows


def scenario_summary(resolver, scenario_id: str = "",
                     return_period_year: int = 0) -> dict:
    scenario = find_scenario(resolver, scenario_id, return_period_year)
    if not scenario:
        return {"error": "scenario not found", "scenario_id": scenario_id,
                "return_period_year": return_period_year}
    sid = scenario["scenario_id"]
    return {
        "scenario": scenario,
        "impact": next((row for row in resolver.impacts if row["scenario_id"] == sid), None),
        "hydrology": [row for row in resolver.hydrology if row.get("scenario_id") == sid],
        "mappable": {
            "object_type": "Cell",
            "filters": {"scenario_id": sid},
            "export_tool": "export_objects_geojson",
        },
    }


def not_wired(name: str):
    def handler(**kwargs):
        return {
            "status": "not_implemented",
            "tool": name,
            "message": "data listing and map export are wired; spatial risk analysis is the next implementation step.",
            "args": kwargs,
        }

    return handler
