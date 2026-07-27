from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from domains.flood.runtime.geojson import export_objects_geojson
from domains.flood.runtime.hydrodynamic_grid import (
    hydrodynamic_grid_stats,
    hydrodynamic_grid_tile,
)
from domains.flood.runtime.impact_analysis import (
    BRIDGE_INFLUENCE_RADIUS_M,
    analyze_inundation_impacts,
)
from domains.flood.runtime.tools import list_mappable_objects
from domains.flood.runtime.workspace import active_workspace_id


ID_FIELDS = {
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
    "EvacuationSite": "evacuation_site_id",
    "EvacuationUnit": "evacuation_unit_id",
    "EvacuationRoute": "evacuation_route_id",
    "DangerArea": "danger_area_id",
    "HydrometeorologicalStation": "station_id",
    "FloodForecast": "forecast_id",
    "InundationForecastCell": "forecast_cell_id",
    "HydrodynamicGridCell": "hydrodynamic_cell_id",
    "EmergencyDirective": "directive_id",
}


class FloodDomainService:
    """Expose flood-domain queries and deterministic runtime functions."""

    def __init__(self, ontology: Any, registry: Any, resolver: Any):
        self.ontology = ontology
        self.registry = registry
        self.resolver = resolver
        self._export_lock = threading.Lock()

    def bootstrap(self, *, llm_enabled: bool) -> dict[str, Any]:
        return {
            "domain": self.ontology.name,
            "title": "珊瑚河洪水应急预警智能体",
            "mappable": list_mappable_objects(self.resolver),
            "counts": {
                "school": self.resolver.count(
                    "Facility", {"facility_type": "school"},
                ),
                "hospital": self.resolver.count(
                    "Facility", {"facility_type": "hospital"},
                ),
                "government": self.resolver.count(
                    "Facility", {"facility_type": "government"},
                ),
            },
            "llm_enabled": llm_enabled,
            "default_context": "基础态 · 领域对象地图",
            "workspace_id": active_workspace_id(),
        }

    def autonomy_cycle(self, force_forecast: bool = False) -> dict:
        return self.registry.call(
            "run_emergency_cycle", force_forecast=force_forecast,
        )

    def forecast(self, force: bool = False) -> dict:
        return self.registry.call(
            "run_flood_forecast", forecast_id="latest", force=force,
        )

    def export_geojson(self, object_type: str, filters: dict,
                       simplify: float = 0) -> tuple[dict, bytes]:
        with self._export_lock:
            result = export_objects_geojson(
                self.resolver,
                object_type,
                filters,
                simplify,
                force=False,
            )
            if "error" in result:
                raise ValueError(result["error"])
            path = Path(result["absolute_path"])
            return result, path.read_bytes()

    def hydrodynamic_grid_stats(
        self,
        forecast_id: str = "latest",
    ) -> dict[str, Any]:
        return hydrodynamic_grid_stats(forecast_id)

    def hydrodynamic_grid_tile(
        self,
        z: int,
        x: int,
        y: int,
        forecast_id: str = "latest",
        wet_only: bool = False,
        time_h: float | None = None,
        tile_crs: str = "wgs84",
    ) -> dict[str, Any]:
        return hydrodynamic_grid_tile(
            z, x, y, forecast_id, wet_only, time_h, tile_crs,
        )

    def analyze_inundation_impacts(
        self,
        forecast_id: str = "latest",
        target_type: str = "all",
        min_depth_m: float = 0.15,
        max_distance_m: float = 10.0,
        time_h: float | None = None,
        bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
    ) -> dict[str, Any]:
        return analyze_inundation_impacts(
            self.resolver,
            forecast_id=forecast_id,
            target_type=target_type,
            min_depth_m=min_depth_m,
            max_distance_m=max_distance_m,
            time_h=time_h,
            bridge_influence_radius_m=bridge_influence_radius_m,
        )

    def get_object(self, object_type: str, object_id: str) -> dict[str, Any]:
        row = self.resolver.query_by_id(object_type, object_id)
        if row:
            return {"object_type": object_type, "object": row}
        id_field = ID_FIELDS.get(object_type)
        rows = (
            self.resolver.query(
                object_type, {id_field: object_id}, limit=1,
            )
            if id_field
            else []
        )
        return {
            "object_type": object_type,
            "object": rows[0] if rows else None,
        }
