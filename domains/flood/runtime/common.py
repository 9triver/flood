from __future__ import annotations

from pathlib import Path
from typing import Any


DOMAIN_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = DOMAIN_DIR.parents[1]
DATA_DIR_CANDIDATES = [
    PROJECT_DIR / "local/source_data/珊瑚河数据",
    PROJECT_DIR / "珊瑚河数据",
]
DATA_DIR = next((path for path in DATA_DIR_CANDIDATES if path.exists()), DATA_DIR_CANDIDATES[0])
DOMAIN_DATA_DIR = DOMAIN_DIR / "data"
OBJECTS_DIR = DOMAIN_DATA_DIR / "objects"
SOURCES_DIR = DOMAIN_DATA_DIR / "sources"

OBJECT_LIBRARY_FILES = {
    "River": "river.jsonl",
    "Watershed": "watershed.jsonl",
    "HydrodynamicBoundary": "hydrodynamic_boundary.jsonl",
    "County": "county.jsonl",
    "Town": "town.jsonl",
    "Reservoir": "reservoir.jsonl",
    "Sluice": "sluice.jsonl",
    "Bridge": "bridge.jsonl",
    "Facility": "facility.jsonl",
    "HydraulicStructure": "hydraulic_structure.jsonl",
    "Road": "road.jsonl",
    "BridgeRoadLink": "bridge_road_link.jsonl",
    "Place": "place.jsonl",
    "Transfer": "transfer.jsonl",
    "Route": "route.jsonl",
    "Risk": "risk.jsonl",
    "HydroStation": "hydro_station.jsonl",
    "Hydrology": "hydrology.jsonl",
}

MAPPABLE_OBJECTS = {
    "River": {
        "label": "珊瑚河",
        "role": "base",
        "style": {"type": "line", "color": "#0e7490", "weight": 4},
    },
    "Watershed": {
        "label": "珊瑚河流域",
        "role": "base",
        "style": {"type": "fill", "color": "#111827", "weight": 1, "fillColor": "#9bc4df", "fillOpacity": 0.1},
    },
    "County": {
        "label": "县级边界",
        "role": "base",
        "style": {"type": "line", "color": "#64748b", "weight": 1},
    },
    "Town": {
        "label": "乡镇边界",
        "role": "base",
        "style": {"type": "fill", "color": "#475569", "weight": 1, "fillColor": "#facc15", "fillOpacity": 0.08},
    },
    "Road": {
        "label": "道路",
        "role": "base",
        "style": {"type": "line", "color": "#6b7280", "weight": 2},
    },
    "Reservoir": {
        "label": "水库",
        "role": "asset",
        "style": {"type": "circle", "color": "#2563eb", "radius": 5, "stroke": "#ffffff"},
    },
    "Sluice": {
        "label": "水闸",
        "role": "asset",
        "style": {"type": "circle", "color": "#0891b2", "radius": 5, "stroke": "#ffffff"},
    },
    "Bridge": {
        "label": "桥梁",
        "role": "asset",
        "style": {"type": "circle", "color": "#111827", "radius": 5, "stroke": "#ffffff"},
    },
    "Facility": {
        "label": "重要设施",
        "role": "asset",
        "style": {"type": "circle", "color": "#dc2626", "radius": 5, "stroke": "#ffffff"},
    },
    "HydraulicStructure": {
        "label": "水利工程设施",
        "role": "asset",
        "style": {"type": "circle", "color": "#0f766e", "radius": 5, "stroke": "#ffffff"},
    },
    "Place": {
        "label": "安置地点",
        "role": "evacuation",
        "style": {"type": "circle", "color": "#16a34a", "radius": 5, "stroke": "#ffffff"},
    },
    "Transfer": {
        "label": "转移安排",
        "role": "evacuation",
        "style": {"type": "circle", "color": "#f97316", "radius": 5, "stroke": "#ffffff"},
    },
    "Route": {
        "label": "路线",
        "role": "evacuation",
        "style": {"type": "line", "color": "#ef4444", "weight": 3},
    },
    "Risk": {
        "label": "危险区",
        "role": "risk",
        "style": {"type": "circle", "color": "#b91c1c", "radius": 5, "stroke": "#ffffff"},
    },
    "HydroStation": {
        "label": "水文测站",
        "role": "hydrology",
        "style": {"type": "circle", "color": "#0284c7", "radius": 5, "stroke": "#ffffff"},
    },
    "HydrodynamicCell": {
        "label": "水动力模型网格",
        "role": "forecast",
        "style": {"type": "fill", "fillColor": "#dc2626", "fillOpacity": 0.42, "color": "#991b1b", "weight": 0.35},
    },
}


def apply_filters(rows: list[dict], filters: dict[str, Any] | None) -> list[dict]:
    result = list(rows)
    for key, value in (filters or {}).items():
        field, op = key.split("__", 1) if "__" in key else (key, "eq")
        if op == "like":
            result = [row for row in result if str(value) in str(row.get(field, ""))]
        elif op == "in":
            values = {str(item) for item in filter_values(value)}
            result = [row for row in result if str(row.get(field, "")) in values]
        elif op == "ne":
            result = [row for row in result if row.get(field) != value]
        elif op == "gt":
            result = [row for row in result if row.get(field) > value]
        elif op == "gte":
            result = [row for row in result if row.get(field) >= value]
        elif op == "lt":
            result = [row for row in result if row.get(field) < value]
        elif op == "lte":
            result = [row for row in result if row.get(field) <= value]
        else:
            result = [row for row in result if row.get(field) == value]
    return result


def filter_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def apply_order(rows: list[dict], order_by: str | None) -> list[dict]:
    if not order_by:
        return rows
    reverse = order_by.startswith("-")
    field = order_by.lstrip("-")
    return sorted(rows, key=lambda row: row.get(field), reverse=reverse)


def apply_window(rows: list[dict], limit: int | None,
                 offset: int | None) -> list[dict]:
    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    return rows


def id_field(object_type: str) -> str:
    return {
        "River": "river_id",
        "Watershed": "watershed_id",
        "HydrodynamicBoundary": "boundary_id",
        "County": "county_id",
        "Town": "town_id",
        "Reservoir": "reservoir_id",
        "Sluice": "sluice_id",
        "HydraulicStructure": "structure_id",
        "Road": "road_id",
        "Bridge": "bridge_id",
        "BridgeRoadLink": "bridge_road_link_id",
        "Facility": "facility_id",
        "Place": "place_id",
        "Transfer": "transfer_id",
        "Route": "route_id",
        "Risk": "risk_id",
        "HydroStation": "station_id",
        "ForecastRun": "forecast_id",
        "ForecastCell": "forecast_cell_id",
        "HydrodynamicCell": "hydrodynamic_cell_id",
        "Hydrology": "hydrology_id",
    }.get(object_type, f"{object_type.lower()}_id")


def rel(path: Path | str) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(PROJECT_DIR.resolve()))
    except ValueError:
        return str(path)
