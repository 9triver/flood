"""Resolve explicit impact-analysis targets before any spatial calculations."""
from __future__ import annotations

from .common import apply_filters, id_field


class ImpactScope:
    def __init__(self, resolver, target_types: list[str], object_ids=None, filters=None):
        self.resolver = resolver
        self.overrides = {}
        self.explicit = object_ids is not None or bool(filters)
        if filters is not None and not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        if object_ids is not None and (not isinstance(object_ids, list) or any(not isinstance(value, str) or not value for value in object_ids)):
            raise ValueError("object_ids must be an array of nonempty strings")
        if self.explicit and len(target_types) != 1:
            raise ValueError("指定 ID 或过滤条件时必须指定一个 target_type，不能使用 all")
        self.rows = {kind: list(resolver.query(kind)) for kind in target_types}
        if self.explicit:
            kind = target_types[0]
            rows = self.rows[kind]
            fields = {field for row in rows for field in row} | {id_field(kind)}
            if rows and any(key.split("__", 1)[0] not in fields for key in (filters or {})):
                raise ValueError(f"unsupported filter field for {kind}")
            if object_ids is not None:
                ids = set(object_ids)
                known = {str(row[id_field(kind)]) for row in rows}
                missing = ids - known
                if missing:
                    raise ValueError(f"unknown {kind} IDs: {sorted(missing)}")
                rows = [row for row in rows if str(row[id_field(kind)]) in ids]
            self.rows[kind] = apply_filters(rows, filters)
            self.overrides[kind] = self.rows[kind]
            if kind == "RoadRoute":
                ids = {str(value) for route in self.rows[kind] for value in route.get("road_ids", [])}
                self.overrides["Road"] = [row for row in resolver.query("Road") if str(row["road_id"]) in ids]
        self.description = {
            "mode": "selected" if self.explicit else "all_of_type",
            "filters": filters or {},
            "requested_object_ids": list(dict.fromkeys(object_ids)) if object_ids is not None else None,
            "matched_object_ids": {kind: [str(row[id_field(kind)]) for row in rows] for kind, rows in self.rows.items()},
            "matched_count": sum(len(rows) for rows in self.rows.values()),
        }

    def query(self, object_type, filters=None):
        if object_type in self.overrides:
            return apply_filters(self.overrides[object_type], filters)
        return self.resolver.query(object_type, filters) if filters else self.resolver.query(object_type)
