from __future__ import annotations


DOMAIN_BASE = "/flood/shanhu"
ASSETS_BASE = f"{DOMAIN_BASE}/assets"
SENSORS_BASE = f"{DOMAIN_BASE}/sensors"
STATIONS_BASE = f"{SENSORS_BASE}/stations"
PRODUCTS_BASE = f"{DOMAIN_BASE}/products"
FORECASTS_BASE = f"{PRODUCTS_BASE}/forecasts"
ROUTES_BASE = f"{PRODUCTS_BASE}/routes"
ASSESSMENTS_BASE = f"{PRODUCTS_BASE}/assessments"
MODEL_PATH = f"{DOMAIN_BASE}/models/hydrodynamic/cnn-v2"
ROUTING_PATH = f"{DOMAIN_BASE}/services/routing/amap"
SCENARIO_PATH = f"{DOMAIN_BASE}/scenarios/flood-emergency"


PRODUCT_COLLECTIONS = {
    "forecast": "forecasts",
    "route": "routes",
    "assessment": "assessments",
}


def asset_path(object_type: str, object_id: str) -> str:
    return f"{ASSETS_BASE}/{object_type}/{object_id}"


def asset_index_path(object_type: str) -> str:
    return f"{ASSETS_BASE}/index/by-type/{object_type}"


def station_path(station_id: str) -> str:
    return f"{STATIONS_BASE}/{station_id}"


def station_metric_path(station_id: str, metric: str) -> str:
    return f"{station_path(station_id)}/metrics/{metric}"


def product_collection_path(kind: str) -> str:
    try:
        collection = PRODUCT_COLLECTIONS[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported product kind: {kind}") from exc
    return f"{PRODUCTS_BASE}/{collection}"


def product_path(kind: str, product_id: str) -> str:
    return f"{product_collection_path(kind)}/{product_id}"


def latest_product_path(kind: str) -> str:
    return f"{product_collection_path(kind)}/latest"
