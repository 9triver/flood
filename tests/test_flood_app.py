from __future__ import annotations

import json
import unittest
from pathlib import Path

from server.flood_app import (
    build_agent_task_hint,
    compact_agent_query_result,
    configured_agent_max_turns,
)
from server.presentation.map_tools import register_map_tools
from server.presentation.directive_tools import register_directive_tools
from oag.ontology.schema import Ontology
from oag.tools.registry import ToolRegistry


PROJECT_DIR = Path(__file__).resolve().parents[1]
ONTOLOGY = Ontology.load(PROJECT_DIR / "domains" / "flood" / "ontology.yaml")


class FloodAppConfigTest(unittest.TestCase):
    def test_map_tool_metadata_comes_from_ontology(self):
        tools = ToolRegistry()
        register_map_tools(tools, resolver=None, ontology=ONTOLOGY)

        tool = tools.get("ui_show_objects")
        definition = ONTOLOGY.presentation_tools["ui_show_objects"]
        self.assertEqual(definition.description, tool.description)
        self.assertIn("对象名称映射来自 ontology", tool.usage_prompt)
        self.assertNotIn("{object_aliases}", tool.usage_prompt)
        self.assertFalse(tool.policy.read_only)

    def test_inundation_alert_tool_comes_from_ontology(self):
        tools = ToolRegistry()
        register_map_tools(tools, resolver=None, ontology=ONTOLOGY)

        tool = tools.get("ui_set_inundation_alert")
        result = json.loads(tool.handler({"active": True}))

        self.assertEqual(
            "set_watershed_inundation_alert",
            result["map_actions"][0]["type"],
        )
        self.assertTrue(result["map_actions"][0]["active"])
        self.assertFalse(tool.policy.read_only)

    def test_directive_editor_tool_metadata_comes_from_ontology(self):
        tools = ToolRegistry()
        register_directive_tools(tools, ONTOLOGY)

        tool = tools.get("ui_open_emergency_directive_editor")
        result = json.loads(tool.handler({
            "title": "组织新民村避洪转移",
            "content": "立即组织群众转移。",
            "recipients": "凤翔镇人民政府",
        }))

        self.assertEqual("frontend_directive_editor", result["kind"])
        self.assertEqual("urgent", result["draft"]["priority"])
        self.assertFalse(tool.policy.read_only)

    def test_agent_max_turns_defaults_to_ten(self):
        self.assertEqual(10, configured_agent_max_turns({}))

    def test_agent_max_turns_is_configurable_and_bounded(self):
        self.assertEqual(12, configured_agent_max_turns({"FLOOD_AGENT_MAX_TURNS": "12"}))
        self.assertEqual(1, configured_agent_max_turns({"FLOOD_AGENT_MAX_TURNS": "0"}))
        self.assertEqual(20, configured_agent_max_turns({"FLOOD_AGENT_MAX_TURNS": "99"}))
        self.assertEqual(10, configured_agent_max_turns({"FLOOD_AGENT_MAX_TURNS": "invalid"}))

    def test_agent_query_result_omits_geometry_but_keeps_domain_attributes(self):
        result = compact_agent_query_result(
            '[{"river_id":"shanhu","name":"珊瑚河","geometry":"very-large-geometry"}]'
        )

        self.assertEqual(
            [{
                "river_id": "shanhu",
                "name": "珊瑚河",
                "geometry_available": True,
            }],
            json.loads(result),
        )

    def test_plain_count_question_gets_exact_domain_tool_hint(self):
        hint = build_agent_task_hint("珊瑚河流域内有几个乡镇？", ONTOLOGY)

        self.assertIn('count({"object_type": "Town"})', hint)
        self.assertIn("得到 count 结果后立即回答", hint)


if __name__ == "__main__":
    unittest.main()
