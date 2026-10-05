from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pyproj import Geod
from shapely.geometry import shape

from domains.flood.runtime import geojson
from domains.flood.runtime.common import DOMAIN_DIR
from domains.flood.runtime.rainfall_input import BASIN_AREAS_KM2
from domains.flood.runtime.repository import FloodRepository
from oag.ontology.registry import FunctionRegistry
from oag.ontology.repository import ObjectRepository
from oag.ontology.schema import Ontology
from oag.tools.registry import ToolRegistry
from server.domain_service import FloodDomainService
from server.presentation.map_tools import register_map_tools


class CatchmentTest(unittest.TestCase):
    def setUp(self):
        self.resolver = FloodRepository()
        self.catchment = self.resolver.query_by_id("Catchment", "longtan_upstream")
        self.ontology = Ontology.load(DOMAIN_DIR / "ontology.yaml")

    def test_geometry_is_in_longtan_and_source_area_is_consistent(self):
        geometry = shape(json.loads(self.catchment["geometry"]))
        reservoir = self.resolver.query_by_id("Reservoir", "longtan")
        self.assertTrue(geometry.is_valid)
        self.assertEqual("Polygon", geometry.geom_type)
        self.assertTrue(geometry.covers(shape(json.loads(reservoir["geometry"]))))
        self.assertEqual("EPSG:4326", self.catchment["geometry_crs"])
        self.assertEqual("EPSG:32649", self.catchment["source_crs"])
        self.assertGreater(geometry.bounds[0], 111.33)
        self.assertLess(geometry.bounds[2], 111.41)
        self.assertGreater(geometry.bounds[1], 24.24)
        self.assertLess(geometry.bounds[3], 24.34)
        area = abs(Geod(ellps="WGS84").geometry_area_perimeter(geometry)[0]) / 1e6
        self.assertAlmostEqual(self.catchment["area_km2"], area, delta=.01)
        self.assertEqual(self.catchment["area_km2"], BASIN_AREAS_KM2["reservoir"])

    def test_agent_can_query_links_search_and_display_catchment(self):
        registry = FunctionRegistry()
        registry.register_resolver("flood_repository", self.resolver)
        repository = ObjectRepository(self.ontology, registry)
        rows = repository.query_links("Reservoir", "longtan", "reservoir_catchments")
        self.assertEqual([self.catchment], rows)
        reservoirs = repository.query_links("Catchment", "longtan_upstream", "catchment_reservoir")
        self.assertEqual(["longtan"], [row["reservoir_id"] for row in reservoirs])
        self.assertEqual(1, repository.count("Catchment", {"reservoir_id": "longtan"}))
        self.assertEqual([], repository.query("Catchment", {"reservoir_id": "unknown"}))
        self.assertEqual("longtan_upstream", repository.search_text("集水区", ["Catchment"])[0]["catchment_id"])
        tools = ToolRegistry()
        register_map_tools(tools, self.resolver, self.ontology)
        result = json.loads(tools.get("ui_show_objects").handler({
            "objects": [{"object_type": "Catchment", "object_ids": ["longtan_upstream"]}],
        }))
        self.assertNotIn("error", result)
        self.assertEqual("Catchment", result["map_actions"][0]["object_type"])

    def test_geojson_and_bootstrap_share_the_calculation_area(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(geojson, "geojson_cache_dir", return_value=Path(directory)):
            result = geojson.export_objects_geojson(self.resolver, "Catchment", {"reservoir_id": "longtan"})
            features = json.loads(Path(result["absolute_path"]).read_text())["features"]
        self.assertEqual(1, len(features))
        service = FloodDomainService(self.ontology, None, self.resolver)
        # Grid availability is unrelated to static catchment metadata.
        with patch("server.domain_service.list_mappable_objects", return_value=[]):
            bootstrap = service.bootstrap(llm_enabled=False)
        self.assertEqual("catchment_id", bootstrap["id_fields"]["Catchment"])
        self.assertEqual(features[0]["properties"]["area_km2"], bootstrap["basin_areas_km2"]["reservoir"])
        self.assertEqual("longtan_upstream", bootstrap["reservoir_catchment"]["catchment_id"])


if __name__ == "__main__":
    unittest.main()
