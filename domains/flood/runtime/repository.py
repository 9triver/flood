from __future__ import annotations

import json
from functools import cached_property
from threading import RLock
from typing import Any

from .common import (
    OBJECT_LIBRARY_FILES,
    OBJECTS_DIR,
    apply_filters,
    apply_order,
    apply_window,
    id_field,
    filter_values,
)
from .forecast_query import (
    count_forecast_cells,
    count_forecast_runs,
    query_forecast_cells,
    query_forecast_runs,
)
from .directives import (
    count_emergency_directives,
    query_emergency_directives,
)
from .hydrodynamic_grid import count_hydrodynamic_cells, query_hydrodynamic_cells
from .route_store import read_planned_routes, read_archived_route
from .road_routes import build_road_routes, road_refs, road_route_id


class FloodRepository:
    def __init__(self):
        self._row_cache: dict[str, list[dict]] = {}
        self._road_signature = None
        self._road_lock = RLock()

    def query(self, object_type: str, filters: dict[str, Any] | None = None,
              limit: int | None = None, order_by: str | None = None,
              offset: int | None = None) -> list[dict]:
        if object_type == "FloodForecast":
            return query_forecast_runs(filters, limit, order_by, offset)
        if object_type == "InundationForecastCell":
            return query_forecast_cells(filters, limit, order_by, offset)
        if object_type == "HydrodynamicGridCell":
            return query_hydrodynamic_cells(filters, limit, order_by, offset)
        if object_type == "EmergencyDirective":
            return query_emergency_directives(filters, limit, order_by, offset)
        if object_type == "EvacuationRoute":
            rows = [dict(row) for row in self._rows(object_type)]
            rows.extend(read_planned_routes())
            selected_ids = (filters or {}).get("evacuation_route_id__in")
            if (filters or {}).get("evacuation_route_id") is not None:
                selected_ids = [(filters or {})["evacuation_route_id"]]
            existing = {str(row["evacuation_route_id"]) for row in rows}
            for ident in filter_values(selected_ids) if selected_ids is not None else []:
                archived = read_archived_route(str(ident)) if str(ident) not in existing else None
                if archived:
                    rows.append(archived)
            rows = apply_filters(rows, filters)
            rows = apply_order(rows, order_by)
            return apply_window(rows, limit, offset)
        rows = [dict(row) for row in self._rows(object_type)]
        rows = apply_filters(rows, filters)
        rows = apply_order(rows, order_by)
        return apply_window(rows, limit, offset)

    def count(self, object_type: str, filters: dict[str, Any] | None = None) -> int:
        if object_type == "FloodForecast":
            return count_forecast_runs(filters)
        if object_type == "InundationForecastCell":
            return count_forecast_cells(filters)
        if object_type == "HydrodynamicGridCell":
            return count_hydrodynamic_cells(filters)
        if object_type == "EmergencyDirective":
            return count_emergency_directives(filters)
        return len(self.query(object_type, filters))

    def query_by_id(self, object_type: str, id_value: Any) -> dict | None:
        rows = self.query(object_type, {id_field(object_type): id_value}, limit=1)
        return rows[0] if rows else None

    def search_text(self, keyword: str, object_types: list[str] | None = None,
                    limit: int = 20) -> list[dict]:
        if not keyword:
            return []
        results = []
        searchable_types = object_types or ["RoadRoute"] + [
            item for item in OBJECT_LIBRARY_FILES
            if item not in {
                "FloodForecast",
                "InundationForecastCell",
                "HydrodynamicGridCell",
                "EmergencyDirective",
            }
        ]
        for object_type in searchable_types:
            for row in self.query(object_type):
                matched = [
                    key for key, value in row.items()
                    if isinstance(value, str) and keyword in value
                ]
                if not matched:
                    continue
                result = dict(row)
                result["_object_type"] = object_type
                result["_matched_field"] = ", ".join(matched)
                results.append(result)
                if len(results) >= limit:
                    return results
        return results

    def _rows(self, object_type: str) -> list[dict]:
        if object_type in {"Road", "RoadRoute", "RoadRouteSegment"}:
            with self._road_lock:
                stat = object_library_path("Road").stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if signature != self._road_signature:
                    roads = read_object_library("Road")
                    routes, memberships = build_road_routes(roads)
                    self._row_cache.update({
                        "Road": [{**road, "road_route_ids": [
                            road_route_id(ref) for ref in road_refs(road.get("ref"))
                        ]} for road in roads],
                        "RoadRoute": routes,
                        "RoadRouteSegment": memberships,
                    })
                    self._road_signature = signature
                return self._row_cache[object_type]
        if object_type in self._row_cache:
            return self._row_cache[object_type]
        rows = read_object_library(object_type)
        self._row_cache[object_type] = rows
        return rows

    @cached_property
    def stations(self) -> list[dict]:
        return self._rows("Station")

    @cached_property
    def towns(self) -> list[dict]:
        return self._rows("Town")


def object_library_path(object_type: str):
    filename = OBJECT_LIBRARY_FILES.get(object_type, f"{object_type.lower()}.jsonl")
    return OBJECTS_DIR / filename


def read_object_library(object_type: str) -> list[dict]:
    path = object_library_path(object_type)
    if not path.exists():
        raise FileNotFoundError(
            f"missing flood object library: {path}. "
            "Restore the generated object-library bundle under domains/flood/data/objects."
        )
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
