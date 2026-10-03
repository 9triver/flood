from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oag.ontology.loader import load_domain

from domains.flood.runtime import geojson, repository
from domains.flood.runtime.common import apply_filters
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.road_routes import build_road_routes, road_refs
from server.chat.policy import build_agent_task_hint
from server.domain_service import FloodDomainService
from server.presentation.map_tools import register_map_tools
from oag.tools.registry import ToolRegistry


DOMAIN = Path(__file__).resolve().parents[1] / "domains/flood"


def segment(road_id, ref, name="测试路", longitude=111.3, road_class="motorway"):
    return {
        "road_id": road_id, "ref": ref, "name": name, "road_class": road_class,
        "name_source": "source", "length_m": 100,
        "geometry": json.dumps({"type": "LineString", "coordinates": [
            [longitude, 24.4], [longitude + 0.00001, 24.40001],
        ]}),
    }


class FixtureResolver:
    def __init__(self):
        roads = [
            segment("shared", "G65;G78", "包茂高速"),
            segment("g65", "G65", "包茂高速", longitude=112),
            segment("g78", "G78", "汕昆高速", longitude=112),
            segment("unknown", "", "包茂高速"),
        ]
        routes, memberships = build_road_routes(roads)
        self.rows = {"Road": roads, "RoadRoute": routes, "RoadRouteSegment": memberships}

    def query(self, object_type, filters=None):
        return apply_filters(self.rows.get(object_type, []), filters)


class RoadRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ontology, cls.repository, cls.registry = load_domain(DOMAIN)

    def test_real_inventory_coverage_and_overlap(self):
        resolver = repository.FloodRepository()
        routes = resolver.query("RoadRoute")
        self.assertEqual(12, resolver.count("RoadRoute"))
        self.assertEqual(199, resolver.count("RoadRouteSegment"))
        self.assertEqual(423, resolver.count("Road"))
        grouped = {road_id for route in routes for road_id in route["road_ids"]}
        self.assertEqual(184, len(grouped))
        self.assertEqual(239, sum(not road["road_route_ids"] for road in resolver.query("Road")))
        g65 = resolver.query("RoadRoute", {"ref": "G65"})[0]
        g78 = resolver.query("RoadRoute", {"ref": "G78"})[0]
        self.assertEqual("包茂高速", g65["name"])
        self.assertEqual("汕昆高速", g78["name"])
        self.assertEqual(11, len(set(g65["road_ids"]) & set(g78["road_ids"])))
        g241 = resolver.query("RoadRoute", {"ref": "G241"})[0]
        self.assertEqual("other", g241["road_category"])
        self.assertEqual(4, g241["shared_segment_count"])

    def test_grouping_is_stable_and_uses_only_canonical_ref(self):
        roads = FixtureResolver().rows["Road"]
        roads[-1].update(osm_ref="G65", osm_tags='{"ref":"G65"}')
        expected = build_road_routes(roads)
        self.assertEqual(expected, build_road_routes(list(reversed(roads))))
        routes, memberships = expected
        self.assertEqual(2, len(routes))
        self.assertEqual(4, len(memberships))
        self.assertNotIn("unknown", {row["road_id"] for row in memberships})
        self.assertEqual(["G65", "G78"], road_refs(" g65 ;G65；G78;way/1;未命名"))
        self.assertEqual(2, len(json.loads(routes[0]["geometry"])["coordinates"]))
        self.assertEqual(200, routes[0]["recorded_length_m"])

    def test_missing_geometry_does_not_drop_membership(self):
        road = segment("no_geometry", "S30")
        road["geometry"] = ""
        routes, links = build_road_routes([road])
        self.assertEqual(1, routes[0]["segment_count"])
        self.assertEqual(0, routes[0]["geometry_segment_count"])
        self.assertEqual(1, len(links))

    def test_links_can_be_traversed_in_both_directions(self):
        repo = self.repository
        memberships = repo.query_links("RoadRoute", "road_route_G65", "road_route_segments")
        self.assertEqual(34, len(memberships))
        for membership in memberships:
            segment_id = membership["road_id"]
            link_id = membership["road_route_segment_id"]
            self.assertEqual(segment_id, repo.query_links(
                "RoadRouteSegment", link_id, "road_membership_segment",
            )[0]["road_id"])
            self.assertEqual("road_route_G65", repo.query_links(
                "RoadRouteSegment", link_id, "road_membership_route",
            )[0]["road_route_id"])
            self.assertIn(membership, repo.query_links("Road", segment_id, "road_segment_routes"))

    def test_count_search_object_and_map_tools(self):
        resolver = repository.FloodRepository()
        self.assertEqual(4, resolver.count("RoadRoute", {"road_category": "expressway"}))
        self.assertEqual("G65", resolver.search_text("包茂高速", ["RoadRoute"])[0]["ref"])
        service = FloodDomainService(self.ontology, self.registry, resolver)
        self.assertEqual(21, service.get_object("RoadRoute", "road_route_S30")["object"]["segment_count"])
        mappable = {item["object_type"] for item in service.bootstrap(llm_enabled=False)["mappable"]}
        self.assertIn("RoadRoute", mappable)
        self.assertNotIn("RoadRouteSegment", mappable)
        tools = ToolRegistry()
        register_map_tools(tools, resolver, self.ontology)
        result = json.loads(tools.get("ui_show_objects").handler({
            "objects": [{"object_type": "RoadRoute", "filters": {"ref": "G65"}}],
        }))
        self.assertEqual("RoadRoute", result["map_actions"][0]["object_type"])
        self.assertEqual("1", result["result_cards"][0]["value"])
        self.assertIn('"RoadRoute"', build_agent_task_hint("有多少条道路？", self.ontology))
        self.assertNotIn('"Road"', build_agent_task_hint("有多少条道路？", self.ontology))
        self.assertIn('"Road"', build_agent_task_hint("有多少个路段？", self.ontology))

    def test_source_change_refreshes_routes_and_geojson(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "road.jsonl"
            source.write_text(json.dumps(segment("first", "G65")) + "\n")
            resolver = repository.FloodRepository()
            with (
                patch.object(repository, "object_library_path", return_value=source),
                patch.object(geojson, "object_library_path", return_value=source),
                patch.object(geojson, "geojson_cache_dir", return_value=root / "cache"),
            ):
                first = geojson.export_objects_geojson(resolver, "RoadRoute")
                self.assertEqual(1, resolver.query("RoadRoute")[0]["segment_count"])
                self.assertTrue(geojson.export_objects_geojson(resolver, "RoadRoute")["cached"])
                with source.open("a") as file:
                    file.write(json.dumps(segment("second", "G65")) + "\n")
                newer = Path(first["absolute_path"]).stat().st_mtime_ns + 1_000_000
                os.utime(source, ns=(newer, newer))
                result = geojson.export_objects_geojson(resolver, "RoadRoute")
                self.assertFalse(result["cached"])
                collection = json.loads(Path(result["absolute_path"]).read_text())
                feature = collection["features"][0]
                self.assertEqual(2, feature["properties"]["segment_count"])
                self.assertEqual("MultiLineString", feature["geometry"]["type"])
                self.assertEqual(2, len(feature["geometry"]["coordinates"]))


class RoadRouteImpactTest(unittest.TestCase):
    def analyze(self, target_type, cells=None):
        if cells is None:
            cells = [{
                "forecast_id": "test_forecast", "forecast_cell_id": "cell1", "mesh_cell_id": "mesh1",
                "centroid_lon": 111.3, "centroid_lat": 24.4,
                "depth_m": 0.8, "velocity_mps": 0.4, "risk_level": "medium", "lead_time_h": 1.5,
                "geometry": json.dumps({"type": "Polygon", "coordinates": [[
                    [111.2999, 24.3999], [111.3001, 24.3999], [111.3001, 24.4001],
                    [111.2999, 24.4001], [111.2999, 24.3999],
                ]]}),
            }]
        with (
            patch("domains.flood.runtime.impact_analysis.query_forecast_cells", return_value=cells),
            patch("domains.flood.runtime.impact_analysis.forecast_time_context", return_value={
                "valid_at": "2026-07-03T21:30:00+08:00",
            }),
        ):
            return analyze_inundation_impacts(FixtureResolver(), target_type=target_type, time_h=1.5)

    def test_all_counts_segments_once_and_keeps_route_summary_separate(self):
        result = self.analyze("all")
        self.assertEqual(2, result["total_impacts"])
        self.assertEqual(2, result["summary"]["Road"]["count"])
        self.assertNotIn("RoadRoute", result["summary"])
        self.assertEqual(2, len(result["road_route_impacts"]))
        coverage = result["road_route_coverage"]
        self.assertEqual(1, coverage["ungrouped_segment_count"])
        self.assertEqual(["unknown"], coverage["ungrouped_affected_road_ids"])
        self.assertEqual(2, coverage["affected_segment_count"])

    def test_route_target_retains_segment_evidence_and_time(self):
        result = self.analyze("RoadRoute")
        self.assertEqual("completed", result["status"])
        self.assertEqual(2, result["total_impacts"])
        self.assertEqual(2, result["summary"]["RoadRoute"]["count"])
        self.assertEqual(1.5, result["time_h"])
        self.assertEqual("2026-07-03T21:30:00+08:00", result["analysis_time_at"])
        for impact in result["impacts"]:
            self.assertEqual("RoadRoute", impact["object_type"])
            self.assertEqual(2, impact["recorded_segment_count"])
            self.assertEqual(1, impact["affected_segment_count"])
            self.assertEqual(["shared"], impact["affected_road_ids"])
            self.assertEqual("not_assessed", impact["passability_status"])
            self.assertEqual("shared", impact["segment_impacts"][0]["object_id"])
            self.assertEqual(0.8, impact["depth_m"])
        self.assertEqual(["road_route_G65", "road_route_G78"], result["affected_object_ids"]["RoadRoute"])

    def test_no_forecast_is_not_reported_as_completed_zero_impacts(self):
        result = self.analyze("RoadRoute", cells=[])
        self.assertEqual("no_forecast_cells", result["status"])
        self.assertNotIn("road_route_coverage", result)


if __name__ == "__main__":
    unittest.main()
