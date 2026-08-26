"""Validate Domain OS queries and GIS views from a persisted run."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from domain_os import DomainQueryService, SqliteDomainStore
from domains.flood.forecast_domain import FORECAST_PRODUCT
from domains.flood.impact_domain import (
    IMPACT_PRODUCT,
    create_flood_impact_domain_system,
)
from domains.flood.product_views import FloodProductViews
from domains.flood.runtime.hydrodynamic_grid import lonlat_to_tile


DEFAULT_DATABASE = Path(
    "local/runtime/domain-os/water.flood/domain.sqlite"
)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.database.is_file():
        raise FileNotFoundError(f"Domain OS database not found: {args.database}")
    store = SqliteDomainStore(args.database)
    system = create_flood_impact_domain_system(store=store)
    await system.start()
    queries = DomainQueryService(system.runtime)
    try:
        forecasts = system.runtime.products(product_type=FORECAST_PRODUCT)
        if not forecasts:
            raise RuntimeError("persisted run has no forecast product")
        forecast = forecasts[-1]
        assessments = tuple(
            product
            for product in system.runtime.products(product_type=IMPACT_PRODUCT)
            if product.input_refs == (forecast.product_id,)
            and (product.data.get("parameters") or {}).get("time_h") is None
        )
        if not assessments:
            raise RuntimeError(
                f"forecast has no envelope impact product: {forecast.product_id}"
            )
        assessment = assessments[-1]
        views = FloodProductViews(queries)
        meta = views.forecast_grid_meta(forecast.product_id)
        bbox = meta["forecast"].get("bbox") or meta["bbox"]
        zoom = int(meta["min_tile_zoom"])
        tile_x, tile_y, tile = _wet_tile_sample(
            views,
            forecast.product_id,
            bbox,
            zoom,
        )
        impact = views.impact_assessment(assessment.product_id)
        products = queries.products(limit=500)
        events = queries.events(limit=500)
        projections = queries.projections()
        return {
            "domain_id": queries.domain_id,
            "projection_count": projections["count"],
            "product_count": products["total"],
            "event_count": events["head_cursor"],
            "forecast_product_id": forecast.product_id,
            "impact_product_id": assessment.product_id,
            "forecast_cell_count": meta["forecast"]["flooded_count"],
            "max_depth_m": meta["forecast"]["max_depth_m"],
            "time_step_count": meta["forecast"]["time_step_count"],
            "sample_tile": {
                "z": zoom,
                "x": tile_x,
                "y": tile_y,
                "wet_cell_count": tile["count"],
            },
            "total_impacts": impact["total_impacts"],
            "workspace_copy_required": False,
        }
    finally:
        queries.close()
        await system.stop()
        store.close()


def _wet_tile_sample(
    views: FloodProductViews,
    forecast_product_id: str,
    bbox: dict[str, Any],
    zoom: int,
) -> tuple[int, int, dict[str, Any]]:
    min_x, max_y = lonlat_to_tile(
        float(bbox["min_lon"]),
        float(bbox["min_lat"]),
        zoom,
    )
    max_x, min_y = lonlat_to_tile(
        float(bbox["max_lon"]),
        float(bbox["max_lat"]),
        zoom,
    )
    for tile_x in range(min(min_x, max_x), max(min_x, max_x) + 1):
        for tile_y in range(min(min_y, max_y), max(min_y, max_y) + 1):
            tile = views.forecast_grid_tile(
                zoom,
                tile_x,
                tile_y,
                forecast_product_id,
                wet_only=True,
            )
            if tile["count"]:
                return tile_x, tile_y, tile
    raise RuntimeError("forecast bbox contains no wet GIS tile")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate persisted Domain OS query and GIS compatibility APIs.",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    return parser.parse_args()


def main() -> None:
    result = asyncio.run(run(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
