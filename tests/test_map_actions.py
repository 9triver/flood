from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from oag.ontology.schema import Ontology
from server.presentation.map_actions import (
    MapActionBuilder,
    dedupe_actions,
    tool_result_to_map_event,
)


ONTOLOGY = Ontology.load(PROJECT_DIR / "domains" / "flood" / "ontology.yaml")


class FakeResolver:
    def count(self, object_type, filters):
        return {
            "Road": 3,
            "Reservoir": 1,
        }.get(object_type, 0)


class MapActionBuilderTest(unittest.TestCase):
    def setUp(self):
        self.builder = MapActionBuilder(ONTOLOGY, FakeResolver())

    def test_builds_normal_object_action_and_card(self):
        result = json.loads(self.builder.show_objects(
            {"objects": [{"object_type": "Road", "fit": False}]},
            {"Road"},
        ))

        self.assertEqual("frontend_map_actions", result["kind"])
        self.assertEqual("load_object", result["map_actions"][0]["type"])
        self.assertEqual("Road", result["map_actions"][0]["object_type"])
        self.assertEqual("3", result["result_cards"][0]["value"])

    def test_rejects_object_outside_runtime_scope(self):
        result = json.loads(self.builder.show_objects(
            {"objects": [{"object_type": "Road"}]},
            {"Reservoir"},
        ))

        self.assertIn("outside presentation tool scope", result["error"])

    def test_rejects_invalid_object_ids_and_simplify_tolerance(self):
        invalid_ids = json.loads(self.builder.show_objects({
            "objects": [{"object_type": "Road", "object_ids": "road_1"}],
        }, {"Road"}))
        invalid_tolerance = json.loads(self.builder.show_objects({
            "objects": [{
                "object_type": "Road",
                "simplify_tolerance": "0.1",
            }],
        }, {"Road"}))

        self.assertIn("object_ids", invalid_ids["error"])
        self.assertIn("simplify_tolerance", invalid_tolerance["error"])

    def test_builds_targeted_highlight_actions(self):
        result = json.loads(self.builder.show_objects({
            "objects": [{
                "object_type": "Road",
                "object_ids": ["road_1", "road_2"],
                "highlight": True,
                "show_only_object_ids": True,
            }],
        }, {"Road"}))

        self.assertEqual(
            ["load_object", "clear_highlights", "highlight_objects"],
            [action["type"] for action in result["map_actions"]],
        )
        self.assertTrue(result["map_actions"][0]["replace_object_type"])
        self.assertEqual("2", result["result_cards"][0]["value"])

    @patch("server.presentation.hydrodynamic.hydrodynamic_grid_stats")
    def test_hydrodynamic_result_is_delegated_to_adapter(self, stats):
        stats.return_value = {
            "forecast": {"flooded_count": 12},
            "feature_count": 20,
        }

        result = json.loads(self.builder.show_objects({
            "objects": [{
                "object_type": "HydrodynamicCell",
                "filters": {"forecast_id": "latest"},
                "refresh": True,
            }],
        }, {"HydrodynamicCell"}))

        self.assertEqual(
            ["show_hydrodynamic_mesh", "apply_hydrodynamic_result"],
            [action["type"] for action in result["map_actions"]],
        )
        self.assertEqual("12", result["result_cards"][0]["value"])

    def test_forecast_filter_does_not_relabel_non_hydrodynamic_object(self):
        result = json.loads(self.builder.show_objects({
            "objects": [{
                "object_type": "Reservoir",
                "filters": {"forecast_id": "latest"},
            }],
        }, {"Reservoir"}))

        self.assertEqual("水库", result["result_cards"][0]["title"])

    def test_focus_uses_single_object_id_and_enforces_scope(self):
        focused = json.loads(self.builder.focus_object(
            {"object_type": "Reservoir", "object_id": "longtan"},
            {"Reservoir"},
        ))
        rejected = json.loads(self.builder.focus_object(
            {"object_type": "Road", "object_id": "road_1"},
            {"Reservoir"},
        ))

        self.assertEqual("longtan", focused["map_actions"][0]["object_id"])
        self.assertIn("outside presentation tool scope", rejected["error"])

    def test_event_marker_requires_coordinates(self):
        result = json.loads(self.builder.show_event_marker(
            {"event": {"event_type": "TestEvent"}},
            {"HydroStation"},
        ))

        self.assertEqual(
            "event marker requires longitude and latitude",
            result["error"],
        )

    def test_dedupe_only_removes_exact_duplicate_actions(self):
        actions = dedupe_actions([
            {"type": "load_object", "object_type": "Road", "fit": True},
            {"type": "load_object", "object_type": "Road", "fit": False},
            {"type": "load_object", "object_type": "Road", "fit": True},
        ])

        self.assertEqual(2, len(actions))

    def test_tool_result_decoder_recognizes_frontend_payload(self):
        event = tool_result_to_map_event(json.dumps({
            "kind": "frontend_map_actions",
            "context": "测试",
            "map_actions": [{"type": "reset"}],
            "result_cards": [],
        }))

        self.assertEqual("map_actions", event["type"])
        self.assertEqual("reset", event["map_actions"][0]["type"])


if __name__ == "__main__":
    unittest.main()
