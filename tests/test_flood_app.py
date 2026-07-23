from __future__ import annotations

import json
import unittest

from server.flood_app import (
    build_agent_task_hint,
    compact_agent_query_result,
    configured_agent_max_turns,
    select_user_agent_tools,
)


class FloodAppConfigTest(unittest.TestCase):
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

    def test_plain_count_question_uses_read_only_tool_scope(self):
        tools = select_user_agent_tools("珊瑚河流域内有几个乡镇？")

        self.assertIn("count", tools)
        self.assertIn("query", tools)
        self.assertNotIn("run_flood_forecast", tools)

    def test_plain_count_question_gets_exact_domain_tool_hint(self):
        hint = build_agent_task_hint("珊瑚河流域内有几个乡镇？")

        self.assertIn('count({"object_type": "Town"})', hint)
        self.assertIn("得到 count 结果后立即回答", hint)

    def test_current_impact_question_does_not_expose_forecast(self):
        tools = select_user_agent_tools("当前时刻哪些道路、桥梁受影响？在地图上显示")

        self.assertIn("analyze_inundation_impacts", tools)
        self.assertIn("ui_show_objects", tools)
        self.assertNotIn("run_flood_forecast", tools)

    def test_explicit_reforecast_adds_forecast_tool(self):
        tools = select_user_agent_tools("重新计算预测并分析哪些道路受影响")

        self.assertIn("run_flood_forecast", tools)
        self.assertIn("analyze_inundation_impacts", tools)

    def test_follow_up_destination_request_keeps_route_tool_available(self):
        tools = select_user_agent_tools("那就换一个最近的安置点")

        self.assertIn("plan_evacuation_route", tools)

    def test_route_follow_up_inherits_recent_tool_scope(self):
        tools = select_user_agent_tools(
            "石角小学到凤翔镇卫生院",
            "请重新规划并画出路线",
        )

        self.assertIn("plan_evacuation_route", tools)


if __name__ == "__main__":
    unittest.main()
