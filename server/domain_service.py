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
from domains.flood.runtime.forecast_context import resolve_forecast_context
from domains.flood.runtime.service import FloodRuntimeService
from domains.flood.runtime.common import OBJECT_ID_FIELDS
from domains.flood.runtime.tools import list_mappable_objects
from domains.flood.runtime.workspace import active_workspace_id


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
            "title": "基于大模型的水路联动应急智能体集群应用",
            "id_fields": dict(OBJECT_ID_FIELDS),
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

    def assess_flood_emergency(self, refresh: bool = False) -> dict:
        return self.registry.call(
            "assess_flood_emergency", refresh=refresh,
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
        if forecast_id != "mesh":
            context = resolve_forecast_context(forecast_id, view="envelope")
            if not context["available"]:
                raise ValueError(context["reason"])
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
        if forecast_id != "mesh":
            context = resolve_forecast_context(forecast_id, time_h, "envelope" if time_h is None else "time_slice")
            if not context["available"]:
                raise ValueError(context["reason"])
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
        object_ids: list[str] | None = None,
        filters: dict | None = None,
    ) -> dict[str, Any]:
        return FloodRuntimeService(self.resolver).analyze_inundation_impacts(
            view="envelope" if time_h is None else "time_slice",
            forecast_id=forecast_id,
            target_type=target_type,
            min_depth_m=min_depth_m,
            max_distance_m=max_distance_m,
            time_h=time_h,
            bridge_influence_radius_m=bridge_influence_radius_m,
            object_ids=object_ids,
            filters=filters,
        )

    def get_object(self, object_type: str, object_id: str) -> dict[str, Any]:
        row = self.resolver.query_by_id(object_type, object_id)
        if row:
            return {"object_type": object_type, "object": row}
        identity_field = OBJECT_ID_FIELDS.get(object_type)
        rows = (
            self.resolver.query(
                object_type, {identity_field: object_id}, limit=1,
            )
            if identity_field
            else []
        )
        return {
            "object_type": object_type,
            "object": rows[0] if rows else None,
        }
