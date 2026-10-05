from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope, active_workspace_id
from domains.flood.runtime.forecast_context import get_flood_status, resolve_forecast_context, resolve_routing_context
from domains.flood.runtime.service import FloodRuntimeService
from server.agent_runs import AgentRun, AgentRunManager
from server.chat.service import FloodChatService
from server.chat.side_effects import AgentSideEffects
from server.chat.policy import is_flood_status_question
from oag.ontology.schema import Ontology
from oag.runtime.events import TextEvent


class ForecastContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = WorkspaceManager(Path(self.directory.name))
        patched = patch("domains.flood.runtime.workspace.WORKSPACES", self.manager)
        patched.start()
        self.addCleanup(patched.stop)
        self.first = self.manager.begin_session()["workspace_id"]
        self.root = self.manager.path()
        self.metadata = {"workspace_id": self.first, "forecast_id": "v001", "forecast_input_id": "input-1", "status": "completed",
                         "valid_from": "2026-07-03T08:00:00+08:00", "valid_to": "2026-07-03T10:00:00+08:00"}
        self.write_json(self.root / "boundary_flows/latest_forecast_input.json", {"summary": {
            "boundary_flow_id": "input-1", "window_start": self.metadata["valid_from"], "window_end": self.metadata["valid_to"]}})
        self.write_json(self.root / "forecasts/latest.json", {"forecast_id": "v001"})
        self.write_json(self.root / "forecasts/v001/forecast.json", self.metadata)
        latest = self.root / "forecasts/latest"
        latest.mkdir(parents=True)
        (latest / "max_depth.csv").write_text("cell_id,max_depth\n1,1.5\n2,0.4\n")
        self.write_json(latest / "time_steps.json", {"time_steps_h": [0, 1, 2]})
        np.save(latest / "depth_series.npy", np.array([[0, 0], [0, 0.4], [1.5, 0.4]], dtype=np.float32))

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def start(self, hour=8):
        self.manager.update_manifest(status="paused", simulation_time=f"2026-07-03T{hour:02d}:00:00+08:00")

    def test_fresh_session_retains_history_but_does_not_activate_it(self):
        self.start()
        self.assertTrue(get_flood_status("envelope")["has_inundation"])
        second = self.manager.begin_session()["workspace_id"]
        self.assertNotEqual(second, self.first)
        self.assertTrue((self.root / "forecasts/latest/max_depth.csv").exists())
        self.assertEqual(json.loads((self.root / "manifest.json").read_text())["status"], "archived")
        self.assertEqual(get_flood_status()["status"], "initial_dry")
        self.assertFalse(get_flood_status()["has_inundation"])
        self.assertFalse((self.manager.path() / "forecasts").exists())
        with workspace_scope(self.first):
            self.assertEqual(get_flood_status()["status"], "inactive_workspace")

    def test_server_boot_creates_ready_workspace_before_runtime_construction(self):
        from server.container import build_application
        self.start()
        observation_path = self.root / "boundary_flows/observations/latest.jsonl"
        observation_path.parent.mkdir(parents=True)
        observation_path.write_text("previous observations")
        with patch("server.container.WORKSPACES", self.manager), patch("server.events.runtime.WORKSPACES", self.manager), patch("server.container.FloodApp", return_value=SimpleNamespace(agent=None)):
            application = build_application()
            status = application.event_runtime.status()
        self.assertNotEqual(status["workspace_id"], self.first)
        self.assertEqual(status["playback_phase"], "ready")
        self.assertIsNone(status["simulation_time"])
        self.assertEqual(status["forecast_context"]["status"], "not_started")
        self.assertEqual(observation_path.read_text(), "previous observations")

    def test_current_slice_and_future_envelope_have_different_meanings(self):
        self.start()
        current = get_flood_status()
        future = get_flood_status("envelope")
        self.assertFalse(current["has_inundation"])
        self.assertEqual(current["time_h"], 0)
        self.assertTrue(future["has_inundation"])
        self.assertIsNone(future["analysis_time_at"])
        self.start(hour=9)
        current = get_flood_status()
        self.assertTrue(current["has_inundation"])
        self.assertEqual(current["flooded_count"], 1)
        self.assertEqual(current["analysis_time_at"], "2026-07-03T09:00:00+08:00")

    def test_opening_timeline_does_not_require_a_current_zero_hour_slice(self):
        from server.presentation.map_actions import MapActionBuilder
        from oag.ontology.schema import Ontology
        self.start()
        latest = self.root / "forecasts/latest"
        self.write_json(latest / "time_steps.json", {"time_steps_h": [0.5, 1, 2]})
        ontology = Ontology.load(Path(__file__).resolve().parents[1] / "domains/flood/ontology.yaml")
        builder = MapActionBuilder(ontology, None)
        def show(filters):
            with patch("server.presentation.map_actions.MapActionBuilder.count_object", return_value=2):
                return json.loads(builder.show_objects({"objects": [{"object_type": "HydrodynamicGridCell", "filters": filters}]}, {"HydrodynamicGridCell"}))
        for view in (None, "timeline"):
            filters = {"forecast_id": "latest", **({"view": view} if view else {})}
            result = show(filters)
            self.assertEqual(result["map_actions"][0]["filters"], {"forecast_id": "latest", "view": "timeline"})
        self.assertIn("error", show({"forecast_id": "latest", "view": "current"}))
        self.assertIn("error", show({"forecast_id": "latest", "time_h": 0}))
        self.assertEqual(get_flood_status()["status"], "time_unavailable")
        self.assertEqual(show({"forecast_id": "latest", "time_h": 0.5})["map_actions"][0]["filters"]["time_h"], 0.5)
        (latest / "depth_series.npy").unlink()
        self.assertIn("error", show({"forecast_id": "latest"}))
        self.write_json(self.root / "forecasts/v001/forecast.json", {**self.metadata, "forecast_input_id": "stale"})
        self.assertIn("error", show({"forecast_id": "latest"}))

    def test_old_input_other_workspace_and_expired_time_are_rejected(self):
        self.start()
        for field, value, expected in (("forecast_input_id", "old-input", "stale_input"), ("workspace_id", "old-workspace", "workspace_mismatch")):
            self.write_json(self.root / "forecasts/v001/forecast.json", {**self.metadata, field: value})
            self.assertEqual(get_flood_status()["status"], expected)
        self.write_json(self.root / "forecasts/v001/forecast.json", self.metadata)
        self.assertEqual(get_flood_status("time_slice", 99)["status"], "time_unavailable")
        self.start(hour=11)
        self.assertFalse(get_flood_status()["available"])

    def test_initial_route_ignores_saved_forecast_but_forecast_analysis_still_requires_it(self):
        from domains.flood.runtime import route_planning
        service = FloodRuntimeService(None)
        with patch("domains.flood.runtime.service.run_flood_forecast") as run, patch("domains.flood.runtime.service.analyze_inundation_impacts") as impact:
            for result in (service.run_flood_forecast(), service.analyze_inundation_impacts()):
                self.assertEqual(result["status"], "forecast_unavailable")
            run.assert_not_called(); impact.assert_not_called()
        with patch.object(route_planning, "query_forecast_cells", side_effect=AssertionError("read old forecast")), patch.object(route_planning, "routing_setting", return_value=""):
            result = service.plan_route(start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
        self.assertEqual(result["status"], "routing_engine_unavailable")
        self.assertEqual(result["flood_avoidance"]["validation"], "initial_dry")

    def test_route_and_impact_default_to_current_slice(self):
        from domains.flood.runtime import route_planning
        self.start(hour=9)
        service = FloodRuntimeService(None)
        with patch.object(route_planning, "query_forecast_cells", return_value=[]) as cells, patch.object(route_planning, "routing_setting", return_value=""):
            service.plan_route(start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38)
            self.assertEqual(cells.call_args.args[0]["time_h"], 1)
            service.plan_route(start_lon=111.15, start_lat=24.38, destination_lon=111.16, destination_lat=24.38, view="envelope")
            self.assertNotIn("time_h", cells.call_args.args[0])
        with patch("domains.flood.runtime.service.analyze_inundation_impacts", return_value={}) as impact:
            service.analyze_inundation_impacts()
            self.assertEqual(impact.call_args.args[5], 1)

    def test_only_unstarted_current_state_can_bypass_forecast_files(self):
        self.assertEqual(resolve_routing_context()["constraint_source"], "initial_state")
        self.assertFalse(resolve_routing_context(time_h=1)["available"])
        self.assertFalse(resolve_routing_context(view="envelope")["available"])
        for status, clock in (("paused", None), ("stopped", None), ("active", None), ("ready", "2026-07-03T08:00:00+08:00")):
            self.manager.update_manifest(status=status, simulation_time=clock)
            self.assertFalse(resolve_routing_context()["available"], (status, clock))
        self.start()
        self.assertEqual(resolve_routing_context()["constraint_source"], "forecast")
        self.write_json(self.root / "forecasts/v001/forecast.json", {**self.metadata, "forecast_input_id": "outdated"})
        self.assertFalse(resolve_routing_context()["available"])

    def test_inflight_chat_keeps_original_scope(self):
        captured = []
        streamer = SimpleNamespace(stream_chat=lambda run: captured.append(active_workspace_id()))
        manager = AgentRunManager(streamer)
        run = AgentRun("run", "session", "query")
        self.manager.begin_session()
        manager._execute(run)
        self.assertEqual(captured, [self.first])
        self.assertTrue(all(event["data"]["workspace_id"] == self.first for event in run.events))


class StatusQuestionTests(unittest.TestCase):
    def test_status_questions_only_have_read_tools(self):
        class Agent:
            def pending_tool_name(self, session_id): return None
            def chat_stream(self, message, session_id, allowed_tools=None, **kwargs):
                self.allowed = allowed_tools
                yield TextEvent(content="尚未开始演进。")
        ontology = Ontology.load(Path(__file__).resolve().parents[1] / "domains/flood/ontology.yaml")
        agent = Agent()
        service = FloodChatService(agent, ontology, AgentSideEffects(ontology.presentation_tools))
        service.stream_chat(AgentRun("run", "session", "现在有淹没区么"))
        self.assertEqual(agent.allowed, ["get_flood_status", "ask_user"])
        for text in ("显示当前淹没区", "重新运行预测", "有哪些道路被淹没", "现在有没有淹没的安置点"):
            self.assertFalse(is_flood_status_question(text))
