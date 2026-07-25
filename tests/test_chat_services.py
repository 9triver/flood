from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.agent_runs import AgentRun, AgentRunManager
from server.chat.service import FloodChatService
from server.chat.side_effects import AgentSideEffects
from server.domain_service import FloodDomainService


class FakeChatStreamer:
    def stream_chat(self, run):
        run.append_event("text", {"type": "text", "content": "完成"})


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"function": name, **kwargs}


class FakeResolver:
    def query_by_id(self, object_type, object_id):
        if object_id == "known":
            return {"road_id": object_id}
        return None

    def query(self, object_type, filters, limit=1):
        return []


class ChatServiceBoundaryTest(unittest.TestCase):
    def test_agent_run_owns_event_sequence_and_completion(self):
        manager = AgentRunManager(FakeChatStreamer())
        run = manager.start("session-1", "测试")
        with run.condition:
            if not run.done:
                run.condition.wait(timeout=2)

        self.assertTrue(run.done)
        self.assertEqual(["text", "done"], [item["type"] for item in run.events])
        self.assertEqual([1, 2], [item["seq"] for item in run.events])
        self.assertEqual(run.run_id, run.events[0]["data"]["run_id"])

    def test_chat_service_without_agent_appends_user_visible_message(self):
        run = AgentRun("run-1", "session-1", "显示水库")
        service = FloodChatService(
            agent=None,
            ontology=object(),
            side_effects=AgentSideEffects([]),
        )

        service.stream_chat(run)

        self.assertEqual("text", run.events[0]["type"])
        self.assertIn("未启用 LLM", run.events[0]["data"]["content"])

    def test_side_effect_mailbox_captures_domain_results(self):
        effects = AgentSideEffects([])
        effects.capture_tool_event({
            "tool_name": "run_flood_forecast",
            "session_id": "event-1",
            "result": json.dumps({"forecast": {"status": "completed"}}),
        })
        effects.capture_tool_event({
            "tool_name": "analyze_inundation_impacts",
            "session_id": "event-1",
            "result": json.dumps({"summary": {}, "impacts": []}),
        })

        self.assertEqual(
            "completed",
            effects.pop_forecast_results("event-1")[0]["forecast"]["status"],
        )
        self.assertEqual([], effects.pop_impact_results("event-1")[0]["impacts"])
        self.assertEqual([], effects.pop_forecast_results("event-1"))

    def test_side_effect_mailbox_deduplicates_map_actions(self):
        effects = AgentSideEffects(["ui_show_objects"])
        result = json.dumps({
            "kind": "frontend_map_actions",
            "context": "水库",
            "map_actions": [{
                "type": "load_object",
                "object_type": "Reservoir",
            }],
            "result_cards": [],
        })
        context = {
            "tool_name": "ui_show_objects",
            "session_id": "chat-1",
            "result": result,
        }

        effects.capture_tool_event(context)
        effects.capture_tool_event(context)

        self.assertEqual(1, len(effects.pop_map_events("chat-1")))

    def test_domain_service_delegates_deterministic_functions(self):
        registry = FakeRegistry()
        service = FloodDomainService(
            ontology=object(),
            registry=registry,
            resolver=FakeResolver(),
        )

        forecast = service.forecast(force=True)
        cycle = service.autonomy_cycle(force_forecast=True)
        known = service.get_object("Road", "known")
        missing = service.get_object("Road", "missing")

        self.assertEqual("run_flood_forecast", forecast["function"])
        self.assertEqual("run_emergency_cycle", cycle["function"])
        self.assertEqual("known", known["object"]["road_id"])
        self.assertIsNone(missing["object"])


if __name__ == "__main__":
    unittest.main()
