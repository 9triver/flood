"""Logical numbered roads derived from the canonical segment inventory.

Membership is evidence of a shared road reference, not network connectivity or
complete national-road coverage. Geometry and lengths retain both carriageways.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict


ROAD_ROUTE_SCOPE = "仅包含项目已收录路段，不代表整条道路的完整范围"


def road_refs(value: str | None) -> list[str]:
    return sorted({
        token.strip().upper()
        for token in re.split(r"[;；]", str(value or ""))
        if re.fullmatch(r"[GSXY]\d+", token.strip(), re.IGNORECASE)
    })


def road_route_id(ref: str) -> str:
    return f"road_route_{ref}"


def build_road_routes(roads: list[dict]) -> tuple[list[dict], list[dict]]:
    groups = defaultdict(dict)
    for road in roads:
        for ref in road_refs(road.get("ref")):
            groups[ref][str(road["road_id"])] = road
    routes, memberships = [], []
    for ref, by_id in sorted(groups.items()):
        segments = [by_id[key] for key in sorted(by_id)]
        route_id = road_route_id(ref)
        names = Counter()
        exclusive_names = Counter()
        lines = []
        geometry_segment_count = 0
        for road in segments:
            name = str(road.get("name") or "").strip()
            if name and road.get("name_source") != "generated_stable_id" and not road_refs(name):
                names[name] += 1
                if road_refs(road.get("ref")) == [ref]:
                    exclusive_names[name] += 1
            geometry = json.loads(road.get("geometry") or "{}")
            coordinates = geometry.get("coordinates")
            if geometry.get("type") == "LineString" and coordinates:
                lines.append(coordinates)
                geometry_segment_count += 1
            elif geometry.get("type") == "MultiLineString" and coordinates:
                lines.extend(coordinates)
                geometry_segment_count += 1
            memberships.append({
                "road_route_segment_id": f"{route_id}__{road['road_id']}",
                "road_route_id": route_id,
                "road_id": road["road_id"],
                "ref": ref,
                "membership_basis": "Road.ref",
            })
        candidates = exclusive_names or names
        name = min(candidates, key=lambda item: (-candidates[item], item)) if candidates else ref
        classes = Counter(str(road.get("road_class") or "unclassified") for road in segments)
        highway_count = sum(classes[key] for key in ("motorway", "motorway_link"))
        category = "expressway" if highway_count == len(segments) else "mixed" if highway_count else "other"
        routes.append({
            "road_route_id": route_id,
            "ref": ref,
            "name": name,
            "name_source": "exclusive_segment_name_mode" if exclusive_names else "segment_name_mode" if names else "road_ref",
            "source_names": sorted(names),
            "road_category": category,
            "road_class": min(classes, key=lambda item: (-classes[item], item)),
            "road_ids": [road["road_id"] for road in segments],
            "segment_count": len(segments),
            "geometry_segment_count": geometry_segment_count,
            "shared_segment_count": sum(len(road_refs(road.get("ref"))) > 1 for road in segments),
            "recorded_length_m": round(sum(float(road.get("length_m") or 0) for road in segments), 3),
            "membership_basis": "Road.ref",
            "coverage_scope": ROAD_ROUTE_SCOPE,
            "geometry_type": "MultiLineString",
            "geometry_crs": "EPSG:4326",
            "geometry": json.dumps({"type": "MultiLineString", "coordinates": lines}),
        })
    return routes, memberships
