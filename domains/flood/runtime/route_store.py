"""Persistence for dynamically planned evacuation routes."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from .workspace import workspace_dir
from .forecast_storage import forecast_cycle_path


_ROUTE_WRITE_LOCK = threading.Lock()


def planned_routes_path(*, create: bool = False) -> Path:
    return workspace_dir(create=create) / "routes" / "current.jsonl"


def save_planned_route(route: dict[str, Any]) -> None:
    with _ROUTE_WRITE_LOCK:
        target = planned_routes_path(create=True)
        rows = read_planned_routes()
        slot = route_slot(route)
        rows = [
            row for row in rows
            if row.get("evacuation_route_id") != route.get("evacuation_route_id")
            and route_slot(row) != slot
        ]
        rows.append(route)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Keep prior route versions addressable by issued directives and follow-up reviews.
        for historical in [*read_planned_routes(), route]:
            ident = str(historical.get("evacuation_route_id") or "")
            if re.fullmatch(r"[A-Za-z0-9_-]+", ident):
                archive = target.parent / "archive" / f"{ident}.json"
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    archive.write_text(json.dumps(historical, ensure_ascii=False), encoding="utf-8")
        body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        temp_path = target.with_suffix(".jsonl.tmp")
        temp_path.write_text(f"{body}\n", encoding="utf-8")
        temp_path.replace(target)
        clear_route_geojson_cache()
        # A one-off emergency assessment includes the currently planned routes.
        forecast_cycle_path().unlink(missing_ok=True)


def clear_route_geojson_cache() -> None:
    cache_dir = workspace_dir() / "cache" / "geojson"
    for pattern in ("evacuationroute*.geojson", "route*.geojson"):
        for path in cache_dir.glob(pattern):
            path.unlink(missing_ok=True)


def read_planned_routes() -> list[dict[str, Any]]:
    target = planned_routes_path()
    if not target.exists():
        return []
    return [
        normalize_planned_route(json.loads(line))
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_archived_route(route_id: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(route_id)):
        return None
    path = planned_routes_path().parent / "archive" / f"{route_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def normalize_planned_route(route: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(route)
    aliases = {
        "route_id": "evacuation_route_id",
        "transfer_id": "origin_unit_id",
        "place_id": "destination_site_id",
    }
    for legacy_field, canonical_field in aliases.items():
        if not normalized.get(canonical_field) and normalized.get(legacy_field):
            normalized[canonical_field] = normalized[legacy_field]
        normalized.pop(legacy_field, None)
    if (
        not normalized.get("origin_unit_id")
        and normalized.get("start_object_type") == "EvacuationUnit"
    ):
        normalized["origin_unit_id"] = normalized.get("start_object_id", "")
    return normalized


def route_slot(route: dict[str, Any]) -> str:
    object_type = str(route.get("start_object_type") or "")
    object_id = str(route.get("start_object_id") or "")
    if object_type and object_id:
        return f"{object_type}:{object_id}"
    return str(route.get("evacuation_route_id") or "")


__all__ = [
    "planned_routes_path",
    "save_planned_route",
    "clear_route_geojson_cache",
    "read_planned_routes",
    "normalize_planned_route",
    "route_slot",
]
