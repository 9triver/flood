from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from tests.test_impact_analysis import StaticResolver, flood_cell
from tests.test_map_actions import ONTOLOGY
from server.chat.policy import build_agent_task_hint, is_flood_status_question
from server.chat.agent_factory import validate_query_filters
from domains.flood.runtime.nearby import find_nearby_objects
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.service import FloodRuntimeService
from domains.flood.runtime import route_planning, forecast


class QueryScopeTests(TestCase):
    def test_qualified_counts_never_get_unfiltered_count_hint(self):
        for text in ("平竹村附近有多少个安置点", "受淹道路有多少条", "同古镇有多少个安置点", "3公里内有几个学校"):
            with self.subTest(text=text):
                self.assertNotIn("count({", build_agent_task_hint(text, ONTOLOGY))
        self.assertIn('"object_type": "Town"', build_agent_task_hint("珊瑚河流域内有几个乡镇？", ONTOLOGY))
        self.assertIn('"site_type": "in_place"', build_agent_task_hint("有多少个就地安置点", ONTOLOGY))
        self.assertIsNotNone(validate_query_filters(ONTOLOGY, "EvacuationSite", {"unknown_town_field": "同古镇"}))
        self.assertIsNone(validate_query_filters(ONTOLOGY, "EvacuationSite", {"town_id": "451122108"}))

    def test_complex_questions_keep_analysis_tools_available(self):
        for text in ("查询淹没范围内有多少物资点", "有几个测站被淹没", "如果有淹水应该怎么避险", "现在有淹没区么，帮我显示一下"):
            self.assertFalse(is_flood_status_question(text), text)
        self.assertTrue(is_flood_status_question("现在有淹没区么？"))
        self.assertTrue(is_flood_status_question("未来24小时有没有积水？"))

    def test_nearby_pagination_covers_all_matches_without_duplicates(self):
        repo = FloodRepository()
        ids, offset = [], 0
        while True:
            result = find_nearby_objects(repo, "EvacuationUnit", "43", radius_m=10000, limit=100, offset=offset)
            ids.extend(result["object_ids"])
            if not result["has_more"]:
                self.assertIsNone(result["next_offset"])
                break
            offset = result["next_offset"]
        self.assertGreater(result["total_matched"], 100)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), result["total_matched"])
        empty = find_nearby_objects(repo, "EvacuationUnit", "43", radius_m=10000, offset=len(ids))
        self.assertEqual(empty["object_ids"], [])

    def test_farther_query_does_not_repeat_nearest_points(self):
        repo = FloodRepository()
        first = find_nearby_objects(repo, "EvacuationUnit", "43")
        next_group = find_nearby_objects(repo, "EvacuationUnit", "43", exclude_object_ids=first["object_ids"])
        self.assertTrue(set(first["object_ids"]).isdisjoint(next_group["object_ids"]))
        band = find_nearby_objects(repo, "EvacuationUnit", "43", min_distance_m=1000, radius_m=3000)
        self.assertTrue(all(1000 <= row["distance_m"] <= 3000 for row in band["results"]))
        self.assertLess(band["total_matched"], band["total_within_radius"])


class ScopedImpactTests(TestCase):
    def setUp(self):
        self.repo = StaticResolver({"EvacuationSite": [
            {"evacuation_site_id": "a", "name": "A", "town_id": "t1", "longitude": 111.3, "latitude": 24.4},
            {"evacuation_site_id": "b", "name": "B", "town_id": "t2", "longitude": 111.3, "latitude": 24.4},
        ]})
        cells = patch("domains.flood.runtime.impact_analysis.query_forecast_cells", return_value=[flood_cell("1", 111.3, 24.4, 0.8)])
        cells.start(); self.addCleanup(cells.stop)

    def test_ids_and_filters_intersect_without_expanding(self):
        result = analyze_inundation_impacts(self.repo, target_type="EvacuationSite", object_ids=["a", "b"], filters={"town_id": "t1"})
        self.assertEqual(result["total_impacts"], 1)
        self.assertEqual(result["affected_object_ids"]["EvacuationSite"], ["a"])
        self.assertEqual(result["analysis_scope"]["matched_object_ids"], {"EvacuationSite": ["a"]})
        empty = analyze_inundation_impacts(self.repo, target_type="EvacuationSite", object_ids=[])
        self.assertEqual(empty["total_impacts"], 0)
        self.assertEqual(empty["analysis_scope"]["matched_count"], 0)
        for params in ({"target_type": "all", "object_ids": ["a"]}, {"target_type": "EvacuationSite", "object_ids": ["missing"]}, {"target_type": "EvacuationSite", "filters": {"typo": 1}}):
            self.assertEqual(analyze_inundation_impacts(self.repo, **params)["status"], "invalid_scope")

    def test_road_route_selection_only_analyzes_its_member_segments(self):
        def road(ident):
            return {"road_id": ident, "name": ident, "geometry": json.dumps({"type": "LineString", "coordinates": [[111.2999, 24.4], [111.3001, 24.4]]})}
        def route(ident, member):
            return {"road_route_id": ident, "name": ident, "ref": ident, "road_ids": [member], "segment_count": 1, "geometry_segment_count": 1}
        repo = StaticResolver({"Road": [road("r1"), road("r2")], "RoadRoute": [route("A", "r1"), route("B", "r2")]})
        result = analyze_inundation_impacts(repo, target_type="RoadRoute", object_ids=["A"])
        self.assertEqual(result["total_impacts"], 1)
        self.assertEqual(result["impacts"][0]["affected_road_ids"], ["r1"])
        self.assertEqual(result["analysis_scope"]["matched_object_ids"]["RoadRoute"], ["A"])


class RouteStateTests(TestCase):
    def test_initial_route_does_not_read_forecast(self):
        service = FloodRuntimeService(None)
        points = [[111.15, 24.38], [111.16, 24.38]]
        path = {"points": {"coordinates": points}, "matched_endpoints": {"coordinates": points}, "distance": 1015, "time": 1000000, "candidate_index": 1}
        with patch.object(route_planning, "resolve_routing_context", return_value={"available": True, "constraint_source": "initial_state", "time_h": None}), patch.object(route_planning, "query_forecast_cells", side_effect=AssertionError("initial route read forecast")), patch.object(route_planning, "forecast_time_context", side_effect=AssertionError("initial route read clock")), patch.object(route_planning, "routing_setting", side_effect=lambda key, default: "test" if key == "AMAP_WEB_SERVICE_KEY" else default), patch.object(route_planning, "call_amap", return_value={}), patch.object(route_planning, "amap_route_paths", return_value=[path]), patch.object(route_planning, "save_planned_route") as save:
            result = service.plan_route(start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["route"]["flood_validation"], "initial_dry")
        self.assertEqual(result["route"]["forecast_id"], "")
        self.assertIn("避洪", result["route"]["name"])
        self.assertTrue(result["flood_avoidance"]["requested"])
        self.assertFalse(result["flood_avoidance"]["forecast_verified"])
        save.assert_called_once()

    def test_missing_prediction_after_start_cannot_be_disabled(self):
        service = FloodRuntimeService(None)
        with patch.object(route_planning, "resolve_routing_context", return_value={"available": False, "reason": "no forecast"}), patch.object(route_planning, "call_amap") as engine:
            result = service.plan_route(start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
            self.assertEqual(result["status"], "forecast_unavailable")
            engine.assert_not_called()
        with self.assertRaises(TypeError):
            service.plan_route(routing_mode="normal")

    def test_starting_playback_during_route_request_invalidates_initial_result(self):
        initial = {"available": True, "constraint_source": "initial_state", "time_h": None}
        unknown = {"available": False, "reason": "演进已开始，预测未就绪"}
        with patch.object(route_planning, "resolve_routing_context", side_effect=[initial, unknown]), patch.object(route_planning, "routing_setting", side_effect=lambda key, default: "test" if key == "AMAP_WEB_SERVICE_KEY" else default), patch.object(route_planning, "call_amap", return_value={}), patch.object(route_planning, "save_planned_route") as save:
            result = route_planning.plan_route(None, start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
        self.assertEqual(result["status"], "forecast_unavailable")
        save.assert_not_called()

    def test_single_assessment_cannot_trigger_forecast(self):
        with patch.object(forecast, "run_flood_forecast", side_effect=AssertionError("assessment ran CNN")), patch.object(forecast, "read_forecast_runs", return_value=[]):
            self.assertEqual(forecast.assess_flood_emergency(None)["status"], "forecast_unavailable")
        cached = {"assessment_mode": "single", "continuous": False, "executed_actions": []}
        with patch.object(forecast, "run_flood_forecast", side_effect=AssertionError("assessment ran CNN")), patch.object(forecast, "read_forecast_runs", return_value=[{"status": "completed"}]), patch.object(forecast, "read_cached_emergency_cycle", return_value=cached):
            self.assertEqual(forecast.assess_flood_emergency(None), cached)
