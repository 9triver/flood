from __future__ import annotations

from .common import MAPPABLE_OBJECTS
from .hydrodynamic_grid import hydrodynamic_grid_stats


def list_mappable_objects(resolver, object_type: str = "") -> list[dict]:
    object_types = [object_type] if object_type else list(MAPPABLE_OBJECTS)
    rows = []
    for item in object_types:
        spec = MAPPABLE_OBJECTS.get(item)
        if not spec:
            continue
        if item == "HydrodynamicCell":
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
