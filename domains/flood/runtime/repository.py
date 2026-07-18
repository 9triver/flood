from __future__ import annotations

import json
from functools import cached_property
from typing import Any

from .common import (
    OBJECT_LIBRARY_FILES,
    OBJECTS_DIR,
    apply_filters,
    apply_order,
    apply_window,
    id_field,
)
from .forecast import (
    count_forecast_cells,
    count_forecast_runs,
    query_forecast_cells,
    query_forecast_runs,
)
from .hydrodynamic_grid import count_hydrodynamic_cells, query_hydrodynamic_cells


class FloodRepository:
    def __init__(self):
        self._row_cache: dict[str, list[dict]] = {}

    def query(self, object_type: str, filters: dict[str, Any] | None = None,
              limit: int | None = None, order_by: str | None = None,
              offset: int | None = None) -> list[dict]:
        if object_type == "ForecastRun":
            return query_forecast_runs(self, filters, limit, order_by, offset)
        if object_type == "ForecastCell":
            return query_forecast_cells(self, filters, limit, order_by, offset)
        if object_type == "HydrodynamicCell":
            return query_hydrodynamic_cells(filters, limit, order_by, offset)
        rows = [dict(row) for row in self._rows(object_type)]
        rows = apply_filters(rows, filters)
        rows = apply_order(rows, order_by)
        return apply_window(rows, limit, offset)

    def count(self, object_type: str, filters: dict[str, Any] | None = None) -> int:
        if object_type == "ForecastRun":
            return count_forecast_runs(self, filters)
        if object_type == "ForecastCell":
            return count_forecast_cells(self, filters)
        if object_type == "HydrodynamicCell":
            return count_hydrodynamic_cells(filters)
        return len(self.query(object_type, filters))

    def query_by_id(self, object_type: str, id_value: Any) -> dict | None:
        rows = self.query(object_type, {id_field(object_type): id_value}, limit=1)
        return rows[0] if rows else None

    def search_text(self, keyword: str, object_types: list[str] | None = None,
                    limit: int = 20) -> list[dict]:
        if not keyword:
            return []
        results = []
        searchable_types = object_types or [
            item for item in OBJECT_LIBRARY_FILES
            if item not in {"ForecastRun", "ForecastCell", "HydrodynamicCell"}
        ]
        for object_type in searchable_types:
            for row in self._rows(object_type):
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
        if object_type in self._row_cache:
            return self._row_cache[object_type]
        rows = read_object_library(object_type)
        self._row_cache[object_type] = rows
        return rows

    @cached_property
    def hydrology(self) -> list[dict]:
        return self._rows("Hydrology")

    @cached_property
    def hydro_stations(self) -> list[dict]:
        return self._rows("HydroStation")

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
            "Run `uv run --project agent python scripts/build_flood_objects.py --force`."
        )
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
