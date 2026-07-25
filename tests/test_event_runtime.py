from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.events import EventRuntime
from server.events.agent_processor import EventAgentProcessor
from server.events.factory import make_impact_event, make_inundation_event
from server.serialization import format_sse, parse_json_object


class NoAgentApp:
    agent = None


class NoopPlaybackTracker:
    def mark_forecast_started(self, forecast_input_id):
        return True

    def mark_forecast_completed(self, forecast_input_id):
        return True

    def mark_forecast_failed(self, forecast_input_id):
        return True


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


if __name__ == "__main__":
    unittest.main()
