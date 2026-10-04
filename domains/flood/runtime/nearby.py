"""Find nearby domain objects using their representative WGS84 locations."""
from __future__ import annotations

import math

from pyproj import Geod

from .common import id_field
from .forecast_geometry import row_point

POINT_TYPES = frozenset({"EvacuationUnit", "EvacuationSite", "Station", "Facility", "Bridge", "Sluice", "HydraulicStructure", "Reservoir"})
_GEOD = Geod(ellps="WGS84")


def find_nearby_objects(resolver, reference_object_type: str,
                        reference_object_id: str, target_type: str = "EvacuationSite",
                        radius_m: float = 3000, limit: int = 10, filters: dict | None = None,
                        min_distance_m: float = 0, offset: int = 0,
                        exclude_object_ids: list[str] | None = None) -> dict:
    if reference_object_type not in POINT_TYPES or target_type not in POINT_TYPES:
        return {"error": "附近查询只支持具有代表位置的点位对象", "supported_types": sorted(POINT_TYPES)}
    if isinstance(radius_m, bool) or not isinstance(radius_m, (int, float)) or not math.isfinite(radius_m) or radius_m <= 0:
        return {"error": "radius_m must be finite and greater than zero"}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return {"error": "limit must be an integer between 1 and 100"}
    if isinstance(min_distance_m, bool) or not isinstance(min_distance_m, (int, float)) or not math.isfinite(min_distance_m) or not 0 <= min_distance_m < radius_m:
        return {"error": "min_distance_m must be finite and satisfy 0 <= min_distance_m < radius_m"}
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return {"error": "offset must be a nonnegative integer"}
    if exclude_object_ids is not None and (not isinstance(exclude_object_ids, list) or any(not isinstance(item, str) for item in exclude_object_ids)):
        return {"error": "exclude_object_ids must be an array of strings"}
    excluded = set(exclude_object_ids or [])
    supported_fields = {"name", "source_name", "town_id", "county_id"}
    supported_fields.update({"EvacuationSite": {"site_type"}, "Facility": {"facility_type"}, "Station": {"station_type"}}.get(target_type, set()))
    if filters is not None and (not isinstance(filters, dict) or any(key.partition("__")[0] not in supported_fields or key.partition("__")[2] not in {"", "eq", "ne", "in", "like"} for key in filters)):
        return {"error": "unsupported nearby filter", "supported_fields": sorted(supported_fields)}
    reference = resolver.query_by_id(reference_object_type, reference_object_id)
    origin = _point(reference)
    if origin is None:
        return {"error": "参考对象不存在或缺少有效坐标"}
    matches = []
    within_radius = 0
    unlocated = []
    for row in resolver.query(target_type, filters):
        object_id = str(row[id_field(target_type)])
        if target_type == reference_object_type and object_id == reference_object_id:
            continue
        point = _point(row)
        if point is None:
            unlocated.append(object_id)
            continue
        _, _, distance = _GEOD.inv(*origin, *point)
        if distance <= radius_m:
            within_radius += 1
        if min_distance_m <= distance <= radius_m and object_id not in excluded:
            matches.append({"object_type": target_type, "object_id": object_id,
                            "name": row.get("name", object_id), "site_type": row.get("site_type"), "distance_m": round(distance, 1), "_distance": distance})
    matches.sort(key=lambda row: (row["_distance"], row["object_id"]))
    results = [{key: value for key, value in row.items() if key != "_distance"} for row in matches[offset:offset + limit]]
    has_more = offset + len(results) < len(matches)
    return {"status": "partial" if unlocated else "completed",
            "reference": {"object_type": reference_object_type, "object_id": reference_object_id,
                          "name": reference.get("name"), "longitude": origin[0], "latitude": origin[1]},
            "target_type": target_type, "radius_m": radius_m, "limit": limit, "filters": filters or {},
            "distance_basis": "WGS84 代表位置之间的直线距离，不是道路通行距离",
            "total_within_radius": within_radius, "total_matched": len(matches), "returned_count": len(results),
            "min_distance_m": min_distance_m, "excluded_object_ids": sorted(excluded),
            "offset": offset, "next_offset": offset + len(results) if has_more else None,
            "has_more": has_more, "truncated": offset > 0 or has_more,
            "object_ids": [row["object_id"] for row in results], "results": results,
            "unlocated_object_ids": unlocated}


def _point(row):
    if not row:
        return None
    try:
        point = row_point(row)
        if point and all(math.isfinite(value) for value in point) and -180 <= point[0] <= 180 and -90 <= point[1] <= 90:
            return point
    except (ValueError, TypeError, KeyError):
        pass
    return None
