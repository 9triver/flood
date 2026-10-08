from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_map_actions import ONTOLOGY, FakeResolver
from server.presentation.map_actions import MapActionBuilder
from server.presentation.hydrodynamic import count_hydrodynamic
from server.agent_runs import AgentRun, AgentRunManager
from domains.flood.runtime.nearby import find_nearby_objects
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope
from domains.flood.runtime import route_planning


class MapToolSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.builder = MapActionBuilder(ONTOLOGY, FakeResolver())

    def show(self, **kwargs):
        return json.loads(self.builder.show_objects({"objects": [{"object_type": "Road", **kwargs}]}, {"Road"}))

    def test_empty_selection_never_expands_or_clears(self):
        for mode in ("add", "replace"):
            result = self.show(object_ids=[], mode=mode, highlight=True)
            self.assertEqual(result["map_actions"], [])
            self.assertEqual(result["status"], "no_matches")
            self.assertEqual(result["result_cards"][0]["value"], "0")

    def test_subset_count_and_selection_do_not_depend_on_highlight(self):
        for highlight in (True, False):
            result = self.show(object_ids=["road_1", "road_1"], highlight=highlight)
            self.assertEqual(result["result_cards"][0]["value"], "1")
            self.assertEqual(result["map_actions"][0]["object_ids"], ["road_1"])
            self.assertEqual(result["map_actions"][0]["mode"], "add")
            self.assertEqual(result["selections"][0]["selection_id"], result["map_actions"][0]["selection_id"])
            self.assertNotIn("受影响", result["result_cards"][0]["detail"])
            self.assertEqual(result["status"], "pending")

    def test_invalid_ids_filters_and_legacy_flags_are_rejected(self):
        for args in ({"object_ids": ["unknown"]}, {"object_ids": ["road_1"], "filters": {"road_id": "road_2"}},
                     {"filters": {"typo__ne": "x"}}, {"filters": {"road_id__bogus": "road_1"}},
                     {"filters": []}, {"object_ids": None}, {"show_only_object_ids": True}):
            with self.subTest(args=args):
                self.assertIn("error", self.show(**args))

    def test_replace_runs_once_per_type_before_additions(self):
        result = json.loads(self.builder.show_objects({"objects": [
            {"object_type": "Road", "object_ids": ["road_1"]},
            {"object_type": "Road", "object_ids": ["road_2"], "mode": "replace"},
            {"object_type": "Road", "object_ids": ["road_3"], "mode": "replace"},
        ]}, {"Road"}))
        self.assertEqual([a["mode"] for a in result["map_actions"]], ["replace", "add", "add"])
        self.assertEqual(len(result["selections"]), 3)

    def test_missing_focus_is_error_and_hide_is_precise(self):
        for args in ({}, {"object_type": "Road"}, {"object_type": "Road", "object_id": "unknown"}):
            self.assertIn("error", json.loads(self.builder.focus_object(args, {"Road"})))
        hidden = json.loads(self.builder.hide_objects({"object_type": "Road", "object_ids": []}, {"Road"}))
        self.assertEqual(hidden["map_actions"], [{"type": "hide_objects", "object_type": "Road", "object_ids": []}])
        hidden = json.loads(self.builder.hide_objects({"selection_id": "selected"}, {"Road"}))
        self.assertEqual(hidden["map_actions"][0]["selection_id"], "selected")
        self.assertIn("error", json.loads(self.builder.hide_objects({}, {"Road"})))

    @patch("server.presentation.hydrodynamic.hydrodynamic_grid_stats", return_value={"forecast": {"flooded_count": 0}, "feature_count": 100})
    @patch("server.presentation.hydrodynamic.resolve_forecast_context", return_value={"available": True, "time_h": 6})
    def test_hydro_zero_is_zero_and_mesh_is_additive(self, context, stats):
        self.assertEqual(count_hydrodynamic("HydrodynamicGridCell", {"forecast_id": "latest"}), 0)
        def show(filters, **kwargs):
            return json.loads(self.builder.show_objects({"objects": [{"object_type": "HydrodynamicGridCell", "filters": filters, **kwargs}]}, {"HydrodynamicGridCell"}))
        self.assertFalse(show({})["map_actions"][0]["mesh_only"])
        self.assertTrue(show({"forecast_id": "latest", "time_h": 6}, fit=True)["map_actions"][0]["fit"])
        for filters in ({"forecast_id": "latest", "depth_m__gt": 1}, {"time_h": 6}, {"forecast_id": "latest", "view": "envelope", "time_h": 6}):
            self.assertIn("error", show(filters))

    def test_receipts_must_correspond_to_dispatched_operations(self):
        manager = AgentRunManager(None)
        run = AgentRun("run", "session", "message")
        manager._runs["run"] = run
        receipt = {"operation_id": "op", "status": "completed", "actions": []}
        self.assertFalse(manager.record_map_receipt("run", receipt))
        run.append_event("map_actions", {"operation_id": "op", "map_actions": []})
        self.assertTrue(manager.record_map_receipt("run", receipt))
        self.assertTrue(manager.record_map_receipt("run", receipt))
        self.assertEqual(sum(event["type"] == "map_action_receipt" for event in run.events), 1)


class NearbyAndForecastTests(unittest.TestCase):
    def test_real_pingzhu_nearby_is_sorted_and_bounded(self):
        result = find_nearby_objects(FloodRepository(), "EvacuationUnit", "43", radius_m=1000, limit=100)
        self.assertIn("shelter_235", result["object_ids"])
        self.assertTrue(all(row["distance_m"] <= 1000 for row in result["results"]))
        self.assertEqual([row["distance_m"] for row in result["results"]], sorted(row["distance_m"] for row in result["results"]))
        shelters = find_nearby_objects(FloodRepository(), "EvacuationUnit", "43", radius_m=1000, filters={"site_type": "shelter"})
        self.assertIn("shelter_235", shelters["object_ids"])
        self.assertTrue(all(row["site_type"] == "shelter" for row in shelters["results"]))
        self.assertIn("error", find_nearby_objects(FloodRepository(), "EvacuationUnit", "43", filters={"station_type": "hydrological"}))
        empty = find_nearby_objects(FloodRepository(), "EvacuationUnit", "43", radius_m=0.01)
        self.assertEqual(empty["object_ids"], [])
        self.assertIn("error", find_nearby_objects(FloodRepository(), "EvacuationUnit", "unknown"))

    def test_dynamic_search_uses_workspace_records(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            wid = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager), workspace_scope(wid):
                repo = FloodRepository()
                self.assertEqual(repo.search_text("平竹", ["FloodForecast"]), [])
                results = repo.search_text("平竹", ["FloodForecast", "EvacuationSite"])
                self.assertEqual(results[0]["evacuation_site_id"], "shelter_235")

    def test_missing_forecast_stops_before_route_engine_or_save(self):
        with patch.object(route_planning, "resolve_routing_context", return_value={"available": False, "reason": "missing forecast"}), patch.object(route_planning, "call_amap") as engine, patch.object(route_planning, "save_planned_route") as save:
            result = route_planning.plan_route(None, start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
        self.assertEqual(result["status"], "forecast_unavailable")
        engine.assert_not_called()
        save.assert_not_called()

    def test_zero_wet_cells_with_available_forecast_is_not_missing_forecast(self):
        with patch.object(route_planning, "resolve_routing_context", return_value={"available": True, "constraint_source": "forecast", "time_h": 0}), patch.object(route_planning, "query_forecast_cells", return_value=[]), patch.object(route_planning, "routing_setting", return_value=""):
            result = route_planning.plan_route(None, start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
        self.assertEqual(result["status"], "routing_engine_unavailable")
        self.assertFalse(result["flood_avoidance"]["enabled"])
