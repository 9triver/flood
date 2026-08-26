from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from server.events import EventRuntime
from server.events.agent_processor import EventAgentProcessor
from server.events.factory import make_impact_event, make_inundation_event
from server.events.factory import make_directive_issued_event
from server.events.messages import summarize_event_tool_result
from server.serialization import format_sse, parse_json_object
from oag.runtime.events import (
    ReasoningEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from domains.flood.runtime.workspace import WorkspaceManager


class NoAgentApp:
    agent = None


class NoopPlaybackTracker:
    def mark_forecast_started(self, forecast_input_id):
        return True

    def mark_forecast_completed(self, forecast_input_id):
        return True

    def mark_forecast_failed(self, forecast_input_id):
        return True


class RecordingPlaybackTracker(NoopPlaybackTracker):
    def __init__(self):
        self.failed = []

    def mark_forecast_failed(self, forecast_input_id):
        self.failed.append(forecast_input_id)
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
                display_name="洪水预测请求事件",
            ),
            "InundationGenerated": SimpleNamespace(
                allowed_tools=["ui_set_inundation_alert", "ui_show_objects"],
                automatic_map=automatic_map,
                display_name="预测淹没结果生成事件",
            ),
        })

    def forecast(self, force=False):
        raise AssertionError("event processor must not bypass the agent tool call")


class CapturingEventAgent:
    def __init__(self, captured):
        self.harness = SimpleNamespace(
            ont=SimpleNamespace(
                build_event_prompt=lambda event_type, event: captured.update({
                    "event_type": event_type,
                    "event": event,
                }) or event_type,
            ),
        )

    def chat_stream(self, prompt, session_id, allowed_tools):
        return iter(())


class DomainContextEventApp(FakeEventApp):
    def __init__(self, captured):
        super().__init__([])
        self.agent = CapturingEventAgent(captured)

    def domain_event_context(self, event):
        return {
            "domain_id": "water.flood",
            "access": "read_only",
            "linkage": "explicit",
            "product_refs": [{"product_id": event["payload"]["product_id"]}],
        }


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

        directive = make_directive_issued_event({
            "directive_id": "DIR-20260725-001",
            "workspace_id": "run_1",
            "title": "组织转移",
            "priority": "urgent",
        })
        self.assertEqual("DirectiveIssued", directive["event_type"])
        self.assertEqual("warning", directive["severity"])

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

    def test_agent_event_prompt_includes_domain_os_references(self):
        captured = {}
        app = DomainContextEventApp(captured)
        processor = EventAgentProcessor(
            app=app,
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: None,
            publish_inundation_event=lambda event, generation: None,
            publish_impact_event=lambda event, generation: None,
        )

        processor._run_agent_for_inundation_event({
            "event_type": "InundationGenerated",
            "event_id": "evt_domain",
            "payload": {"product_id": "water.flood.forecast/run/000001"},
        }, generation=1)

        context = captured["event"]["domain_os_context"]
        self.assertEqual("explicit", context["linkage"])
        self.assertEqual(
            "water.flood.forecast/run/000001",
            context["product_refs"][0]["product_id"],
        )

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
            TextEvent(content=(
                "FloodForecastRequired 已完成，后续发布 InundationGenerated。"
            )),
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
        conclusion_trace = next(item[2] for item in timeline if item[1] == "TEXT")
        self.assertIn("洪水预测请求事件", conclusion_trace["detail"])
        self.assertIn("预测淹没结果生成事件", conclusion_trace["detail"])
        self.assertNotIn("FloodForecastRequired", conclusion_trace["detail"])
        self.assertNotIn("InundationGenerated", conclusion_trace["detail"])
        completion_trace = timeline[-2][2]
        self.assertEqual("洪水预测请求事件完成", completion_trace["label"])
        self.assertIn("后续的预测淹没结果生成事件", completion_trace["detail"])
        self.assertIn("不会自动执行", completion_trace["detail"])
        self.assertNotIn("FloodForecastRequired", completion_trace["detail"])
        self.assertNotIn("InundationGenerated", completion_trace["detail"])

    def test_missing_forecast_tool_call_is_visible_and_not_bypassed(self):
        app = FakeEventApp([TextEvent(content="本轮不调用预测工具。")])
        tracker = RecordingPlaybackTracker()
        outputs = []
        processor = EventAgentProcessor(
            app=app,
            playback_runner=tracker,
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: outputs.append(data),
            publish_inundation_event=lambda event, generation: self.fail(
                "inundation event must not be published"
            ),
            publish_impact_event=lambda event, generation: None,
        )

        processor.handle_event({
            "event_type": "FloodForecastRequired",
            "event_id": "evt_missing_tool",
            "source_id": "boundary_v002",
            "correlation_id": "episode_1",
            "severity": "warning",
            "payload": {
                "observation": {},
                "forecast_input": {"boundary_flow_id": "boundary_v002"},
                "forecast_trigger": {"should_run_forecast": True},
            },
        }, generation=1)

        self.assertEqual(["boundary_v002"], tracker.failed)
        error = next(item for item in outputs if item.get("tag") == "ERR")
        self.assertIn("未调用 run_flood_forecast", error["detail"])

    def test_forecast_result_from_another_workspace_is_rejected(self):
        forecast_result = {
            "forecast": {
                "status": "completed",
                "forecast_id": "forecast_latest",
                "forecast_input_id": "boundary_v001",
                "workspace_id": "run_previous",
            },
        }
        app = FakeEventApp([
            ToolCallEvent(
                name="run_flood_forecast",
                args={"forecast_id": "latest"},
            ),
            ToolResultEvent(
                name="run_flood_forecast",
                result=json.dumps(forecast_result, ensure_ascii=False),
            ),
        ])
        tracker = RecordingPlaybackTracker()
        outputs = []
        published = []
        processor = EventAgentProcessor(
            app=app,
            playback_runner=tracker,
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: outputs.append(data),
            publish_inundation_event=lambda event, generation: published.append(event),
            publish_impact_event=lambda event, generation: None,
        )

        processor.handle_event({
            "event_type": "FloodForecastRequired",
            "event_id": "evt_wrong_workspace",
            "source_id": "boundary_v001",
            "workspace_id": "run_current",
            "correlation_id": "episode_1",
            "severity": "warning",
            "payload": {
                "forecast_input": {"boundary_flow_id": "boundary_v001"},
                "forecast_trigger": {"should_run_forecast": True},
            },
        }, generation=1)

        self.assertEqual(["boundary_v001"], tracker.failed)
        self.assertEqual([], published)
        error = next(item for item in outputs if item.get("tag") == "ERR")
        self.assertIn("run_current", error["detail"])
        self.assertIn("run_previous", error["detail"])

    def test_runtime_persists_directive_event_to_current_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager), patch(
                "server.events.runtime.WORKSPACES", manager,
            ):
                runtime = EventRuntime(NoAgentApp())
                runtime.publish_directive_issued({
                    "directive_id": "DIR-20260725-001",
                    "workspace_id": workspace_id,
                    "title": "组织转移",
                    "recipients": "凤翔镇人民政府",
                    "priority": "urgent",
                    "issued_at": "2026-07-25T12:00:00+08:00",
                })

            timeline = (
                manager.path(workspace_id) / "events" / "timeline.jsonl"
            )
            records = [
                json.loads(line)
                for line in timeline.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual("DirectiveIssued", records[0]["data"]["event_type"])

    def test_inundation_stage_sets_alert_without_publishing_impact_event(self):
        alert_result = {
            "kind": "frontend_map_actions",
            "context": "24小时淹没警戒 · 珊瑚河流域",
            "map_actions": [{
                "type": "set_watershed_inundation_alert",
                "active": True,
            }],
            "result_cards": [],
        }
        app = FakeEventApp([
            ReasoningEvent(content="预测期内存在淹没单元。"),
            ToolCallEvent(name="ui_set_inundation_alert", args={"active": True}),
            ToolResultEvent(
                name="ui_set_inundation_alert",
                result=json.dumps(alert_result, ensure_ascii=False),
            ),
            TextEvent(content="已显示流域警戒边界。"),
        ])
        timeline = []
        impact_events = []
        processor = EventAgentProcessor(
            app=app,
            playback_runner=NoopPlaybackTracker(),
            current_generation=lambda: 1,
            append_output=lambda name, data, generation: timeline.append(
                ("trace", data.get("tag"), data)
            ),
            publish_inundation_event=lambda event, generation: None,
            publish_impact_event=lambda event, generation: impact_events.append(event),
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
        self.assertEqual([], impact_events)
        self.assertIn("set_watershed_inundation_alert", timeline[2][2]["detail"])
        completion_trace = timeline[-1][2]
        self.assertEqual("预测淹没结果生成事件完成", completion_trace["label"])
        self.assertNotIn("InundationGenerated", completion_trace["detail"])

    def test_generic_tool_summary_keeps_valid_bounded_json(self):
        detail = summarize_event_tool_result("query", {
            "items": [{"id": index, "name": f"对象 {index}"} for index in range(20)],
            "metadata": {f"field_{index}": index for index in range(20)},
        })
        payload = json.loads(detail.removeprefix("```json\n").removesuffix("\n```"))

        self.assertEqual({"_omitted_items": 15}, payload["items"][-1])
        self.assertGreater(payload["metadata"]["_omitted_fields"], 0)

    def test_impact_tool_summary_prefers_absolute_forecast_time(self):
        detail = summarize_event_tool_result("analyze_inundation_impacts", {
            "status": "completed",
            "forecast_id": "v003",
            "time_h": 1.5,
            "analysis_time_at": "2026-07-03T21:30:00+08:00",
            "total_impacts": 0,
            "summary": {},
            "impacts": [],
        })

        self.assertIn("2026-07-03T21:30:00+08:00（预测 +1.50 h）", detail)


if __name__ == "__main__":
    unittest.main()
