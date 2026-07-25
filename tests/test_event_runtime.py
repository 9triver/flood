from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.events import EventRuntime
from server.events.agent_processor import EventAgentProcessor
from server.events.factory import make_impact_event, make_inundation_event
from server.events.messages import summarize_event_tool_result
from server.serialization import format_sse, parse_json_object
from oag.runtime.events import (
    ReasoningEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class NoAgentApp:
    agent = None


class NoopPlaybackTracker:
    def mark_forecast_started(self, forecast_input_id):
        return True

    def mark_forecast_completed(self, forecast_input_id):
        return True

    def mark_forecast_failed(self, forecast_input_id):
        return True


class FakeEventAgent:
    def __init__(self, events):
        self.events = events
        self.harness = SimpleNamespace(
            ont=SimpleNamespace(build_event_prompt=lambda event_type, event: event_type)
        )

    def chat_stream(self, prompt, session_id, allowed_tools):
        yield from self.events


class FakeEventApp:
    def __init__(self, events):
        self.agent = FakeEventAgent(events)
        automatic_map = SimpleNamespace(
            allowed_action_types=[],
            other_objects="none",
        )
        self.ontology = SimpleNamespace(event_policies={
            "FloodForecastRequired": SimpleNamespace(
                allowed_tools=["run_flood_forecast"],
            ),
            "InundationGenerated": SimpleNamespace(
                allowed_tools=["analyze_inundation_impacts"],
                automatic_map=automatic_map,
            ),
        })

    def forecast(self, force=False):
        return {"forecast": {"status": "completed", "forecast_id": "fallback"}}


class EventRuntimeModuleBoundaryTest(unittest.TestCase):
    def test_sse_serialization_remains_utf8_and_json(self):
        encoded = format_sse("domain_event", {"title": "洪水事件"})
        text = encoded.decode("utf-8")

        self.assertTrue(text.startswith("event: domain_event\ndata: "))
        payload = json.loads(text.split("data: ", 1)[1])
        self.assertEqual("洪水事件", payload["title"])

    def test_json_object_parser_rejects_non_object_payloads(self):
        self.assertEqual({"status": "ok"}, parse_json_object('{"status":"ok"}'))
        self.assertIsNone(parse_json_object('["not", "an", "object"]'))

    def test_event_factories_preserve_correlation_and_source_identity(self):
        source_event = {
            "source_id": "boundary_7",
            "correlation_id": "episode_1",
            "payload": {},
        }
        inundation = make_inundation_event(
            source_event,
            {"forecast": {"forecast_id": "latest", "status": "completed"}},
            "warning",
        )
        impact = make_impact_event({
            "forecast_id": "latest",
            "total_impacts": 1,
            "summary": {"Road": {"critical": 1}},
            "impacts": [{"object_type": "Road", "object_id": "road_1"}],
        }, "event-session")

        self.assertEqual("latest:boundary_7", inundation["source_id"])
        self.assertEqual("episode_1", inundation["correlation_id"])
        self.assertEqual("critical", impact["severity"])
        self.assertEqual("event-session", impact["correlation_id"])

    def test_agent_processor_ignores_stale_generation(self):
        outputs = []
        processor = EventAgentProcessor(
            app=NoAgentApp(),
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 2,
            append_output=lambda name, data, generation: outputs.append(
                (name, data, generation)
            ),
            publish_inundation_event=lambda event, generation: None,
            publish_impact_event=lambda event, generation: None,
        )

        processor.handle_event({
            "event_type": "FloodForecastRequired",
            "event_id": "evt_stale",
            "payload": {},
        }, generation=1)

        self.assertEqual([], outputs)

    def test_no_agent_forecast_event_emits_rule_trace(self):
        outputs = []
        processor = EventAgentProcessor(
            app=NoAgentApp(),
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 3,
            append_output=lambda name, data, generation: outputs.append(
                (name, data, generation)
            ),
            publish_inundation_event=lambda event, generation: None,
            publish_impact_event=lambda event, generation: None,
        )

        processor.handle_event({
            "event_type": "FloodForecastRequired",
            "event_id": "evt_current",
            "severity": "warning",
            "payload": {
                "observation": {},
                "forecast_trigger": {"should_run_forecast": True},
            },
        }, generation=3)

        self.assertEqual("agent_trace", outputs[0][0])
        self.assertEqual("SYSTEM", outputs[0][1]["tag"])
        self.assertTrue(outputs[0][1]["should_run_model"])

    def test_forecast_trace_preserves_event_order_and_stage_boundary(self):
        forecast_result = {
            "forecast": {
                "status": "completed",
                "forecast_id": "forecast_latest",
                "forecast_input_id": "boundary_v001",
                "forecast_cell_count": 65183,
                "inundated_area_km2": 4.9067,
                "max_depth_m": 2.546,
            },
        }
        app = FakeEventApp([
            ReasoningEvent(content="先核对预测触发条件。"),
            ToolCallEvent(name="run_flood_forecast", args={"forecast_id": "latest"}),
            ToolResultEvent(
                name="run_flood_forecast",
                result=json.dumps(forecast_result, ensure_ascii=False),
            ),
            ReasoningEvent(content="模型已经返回有效预测。"),
            TextEvent(content="当前预测请求事件已完成。"),
        ])
        timeline = []
        processor = EventAgentProcessor(
            app=app,
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: timeline.append(
                ("trace", data.get("tag"), data)
            ),
            publish_inundation_event=lambda event, generation: timeline.append(
                ("publish", event["event_type"], event)
            ),
            publish_impact_event=lambda event, generation: None,
        )

        processor.handle_event({
            "event_type": "FloodForecastRequired",
            "event_id": "evt_forecast",
            "source_id": "boundary_v001",
            "correlation_id": "episode_1",
            "severity": "warning",
            "payload": {
                "observation": {},
                "forecast_input": {"boundary_flow_id": "boundary_v001"},
                "forecast_trigger": {"should_run_forecast": True},
            },
        }, generation=1)

        self.assertEqual(
            ["SYSTEM", "THINK", "CALL", "RESULT", "THINK", "TEXT", "DONE"],
            [item[1] for item in timeline if item[0] == "trace"],
        )
        self.assertEqual(("publish", "InundationGenerated"), timeline[-1][:2])
        result_trace = next(item[2] for item in timeline if item[1] == "RESULT")
        self.assertIn("预测淹没单元：65183 个", result_trace["detail"])
        self.assertNotIn('{"forecast":', result_trace["detail"])
        self.assertIn("后续", timeline[-2][2]["detail"])

    def test_impact_child_event_is_published_after_parent_stage_done(self):
        impact_result = {
            "status": "completed",
            "forecast_id": "forecast_latest",
            "summary": {"Road": {"count": 2, "high": 1, "medium": 1}},
            "total_impacts": 2,
            "impacts": [
                {"object_type": "Road", "object_id": "road_1"},
                {"object_type": "Road", "object_id": "road_2"},
            ],
        }
        app = FakeEventApp([
            ReasoningEvent(content="先执行确定性空间分析。"),
            ToolCallEvent(name="analyze_inundation_impacts", args={}),
            ToolResultEvent(
                name="analyze_inundation_impacts",
                result=json.dumps(impact_result, ensure_ascii=False),
            ),
            TextEvent(content="已完成影响分析。"),
        ])
        timeline = []
        processor = EventAgentProcessor(
            app=app,
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: timeline.append(
                ("trace", data.get("tag"), data)
            ),
            publish_inundation_event=lambda event, generation: None,
            publish_impact_event=lambda event, generation: timeline.append(
                ("publish", event["event_type"], event)
            ),
        )

        processor.handle_event({
            "event_type": "InundationGenerated",
            "event_id": "evt_inundation",
            "payload": {},
        }, generation=1)

        self.assertEqual(
            ["THINK", "CALL", "RESULT", "TEXT", "DONE"],
            [item[1] for item in timeline if item[0] == "trace"],
        )
        self.assertEqual(("publish", "ImpactAnalyzed"), timeline[-1][:2])

    def test_generic_tool_summary_keeps_valid_bounded_json(self):
        detail = summarize_event_tool_result("query", {
            "items": [{"id": index, "name": f"对象 {index}"} for index in range(20)],
            "metadata": {f"field_{index}": index for index in range(20)},
        })
        payload = json.loads(detail.removeprefix("```json\n").removesuffix("\n```"))

        self.assertEqual({"_omitted_items": 15}, payload["items"][-1])
        self.assertGreater(payload["metadata"]["_omitted_fields"], 0)


if __name__ == "__main__":
    unittest.main()
