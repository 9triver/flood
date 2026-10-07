from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from domains.flood.runtime import dispatch_trial
from domains.flood.runtime.boundary_flow import BoundaryFlowPlaybackSource, FloodForecastPolicy
from domains.flood.runtime.reservoir_dispatch import DispatchSettings
from domains.flood.runtime.workspace import WorkspaceManager
from tests.test_impact_analysis import StaticResolver, flood_cell


def write_prediction(boundary, folder):
    steps = np.arange(0, 24.5, 0.5)
    upstream = boundary["summary"]["boundaries"]["upstream"]["series"]
    flows = np.interp(steps, [row["time_h"] for row in upstream], [row["flow_m3s"] for row in upstream])
    values = np.stack([flows / 20 * steps / 24, np.zeros(len(steps))], axis=1).astype(np.float32)
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "depth_series.npy", values)
    (folder / "time_steps.json").write_text(json.dumps({"time_steps_h": steps.tolist()}))
    (folder / "max_depth.csv").write_text(f"cell_id,max_depth\n1,{values[:, 0].max()}\n2,0\n")
    return values


class DispatchTrialTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.manager = WorkspaceManager(root / "workspaces")
        self.wid = self.manager.create()["workspace_id"]
        self.stack.enter_context(patch("domains.flood.runtime.workspace.WORKSPACES", self.manager))
        self.root = self.manager.path(self.wid)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["time_period_end", "interval1_rainfall_mm", "interval2_rainfall_mm", "reservoir_rainfall_mm"])
        for index in range(60):
            writer.writerow([(datetime(2026, 7, 1) + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M"), 0, 0, 0])
        rainfall = root / "rainfall.csv"
        rainfall.write_text(stream.getvalue())
        self.source = BoundaryFlowPlaybackSource(
            rainfall, root / "observations.jsonl",
            dispatch_settings=DispatchSettings(mode="OUTFLOW", initial_level_m=246.5, target_outflow_m3s=20),
        )
        self.source.index = 5
        self.observation = self.source.next_observation()
        self.policy = FloodForecastPolicy(
            self.source.rows, total_trigger_m3s=1,
            forecast_input_dir=self.root / "boundary_flows/forecast_inputs",
            latest_forecast_input_path=self.root / "boundary_flows/latest_forecast_input.json",
        )
        self.policy.observe(self.observation)
        self.snapshot = self.policy.latest_forecast_input
        self.input_path = Path(self.snapshot["summary"]["input_path"])
        self.t0 = self.observation["observed_at"]
        self.manager.update_manifest(status="paused", simulation_time=self.t0)
        self.baseline_dir = self.root / "forecasts/v001"
        write_prediction(self.snapshot, self.baseline_dir)
        self.metadata = {
            "workspace_id": self.wid, "status": "completed", "forecast_id": "v001",
            "forecast_input_id": self.snapshot["boundary_flow_id"],
            "valid_from": self.t0, "valid_to": self.snapshot["summary"]["window_end"],
            "generated_at": self.t0, "boundary_flow": json.dumps(self.snapshot["summary"]),
        }
        (self.baseline_dir / "forecast.json").write_text(json.dumps(self.metadata))
        (self.root / "forecasts/latest.json").write_text(json.dumps({"forecast_id": "v001"}))
        self.original_files = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.repo = StaticResolver({"Road": [{
            "road_id": "r1", "name": "X706", "geometry_type": "LineString",
            "geometry": json.dumps({"type": "LineString", "coordinates": [[111.29995, 24.4], [111.30005, 24.4]]}),
        }]})
        self.calls = []
        def predict(boundary, target, *, work_dir):
            self.calls.append(deepcopy(boundary))
            self.assertTrue(work_dir.is_relative_to(self.root / "dispatch_trials"))
            write_prediction(boundary, target.parent)
            return {"status": "completed"}
        self.predict = self.stack.enter_context(patch.object(dispatch_trial, "run_cnn_v2_forecast", side_effect=predict))
        def cells(depths, generated_at, time_h, forecast_id):
            return [{**flood_cell(str(key), 111.3, 24.4, value), "forecast_id": forecast_id,
                     "lead_time_h": time_h or 0} for key, value in depths.items()]
        self.stack.enter_context(patch.object(dispatch_trial, "forecast_cells_from_hydrodynamic_mesh", side_effect=cells))

    def trial(self, settings=None, **kwargs):
        return dispatch_trial.simulate_longtan_dispatch(
            self.repo, settings or {"mode": "OUTFLOW", "target_outflow_m3s": 0},
            "v001", 12, **kwargs,
        )

    def test_trial_starts_at_t0_and_compares_t1_and_the_whole_window(self):
        result = self.trial()
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["applied"])
        self.assertEqual(result["t0"], self.t0)
        self.assertEqual(result["t1"], "2026-07-01T17:00:00+08:00")
        self.assertTrue(result["reservoir_safety"]["candidate"]["passed"])
        self.assertTrue(result["candidate_satisfies_objective"])
        for view in ("selected_time", "window_envelope"):
            comparison = result["comparison"][view]
            self.assertEqual(comparison["baseline"]["affected_count"], 1)
            self.assertEqual(comparison["candidate"]["affected_count"], 0)
        folder = self.root / "dispatch_trials" / result["trial_id"]
        continuation = json.loads((folder / "reservoir.json").read_text())["series"]
        expected = self.snapshot["reservoir_dispatch_context"]["state"]
        self.assertAlmostEqual(continuation[0]["start_storage_1e4m3"], expected["storage_1e4m3"], places=5)
        self.assertEqual(continuation[0]["valid_time"], "2026-07-01T06:00:00+08:00")
        self.assertNotEqual(continuation[0]["start_level_m"], 246.5)
        for key in ("interval1", "interval2", "tonggu"):
            self.assertEqual(self.calls[0]["summary"]["boundaries"][key], self.snapshot["summary"]["boundaries"][key])
        original = self.snapshot["summary"]["boundaries"]["upstream"]["series"]
        candidate = self.calls[0]["summary"]["boundaries"]["upstream"]["series"]
        self.assertEqual(candidate[0], original[0])
        self.assertTrue(all(row["flow_m3s"] == 0 for row in candidate[1:]))
        self.assertEqual(self.source.index, 6)
        self.assertEqual(self.source.dispatch_settings.mode, "OUTFLOW")
        for path, content in self.original_files.items():
            self.assertEqual(path.read_bytes(), content, path)

    def test_unchanged_plan_reproduces_the_baseline(self):
        result = self.trial({"mode": "OUTFLOW", "target_outflow_m3s": 20})
        self.assertEqual(result["status"], "completed", result)
        for view in result["comparison"].values():
            self.assertEqual(view["baseline"], view["candidate"])
            self.assertEqual(view["new_count"], 0)
            self.assertEqual(view["removed_count"], 0)
        expected = self.snapshot["summary"]["boundaries"]["upstream"]["series"]
        self.assertEqual(self.calls[0]["summary"]["boundaries"]["upstream"]["series"], expected)

    def test_chat_tool_runs_the_trial_from_the_selected_forecast(self):
        from oag.harness import Harness
        from oag.ontology.loader import load_domain
        from server.chat.agent_factory import configure_domain_tool_schemas
        from server.chat.analysis_context import analysis_scope, capture_analysis_context, normalize_analysis_tool
        ontology, repo, registry = load_domain(Path(__file__).resolve().parents[1] / "domains/flood")
        harness = Harness(ontology, repo, registry, None, "test")
        configure_domain_tool_schemas(harness)
        harness.hooks.register("pre_tool_call", normalize_analysis_tool)
        selection = {"workspace_id": self.wid, "hydrodynamic_timeline": {
            "active": True, "mode": "time_slice", "forecast_version": "v001",
            "current_hydrodynamic_time_h": 12,
        }}
        with analysis_scope(capture_analysis_context(selection)):
            plan = json.loads(harness.execute_tool("get_longtan_dispatch_plan", {}, session_id="trial").content)
            self.assertEqual(plan["t0"], self.t0, plan)
            result = json.loads(harness.execute_tool("simulate_longtan_dispatch", {
                "settings": {"mode": "OUTFLOW", "target_outflow_m3s": 0}, "object_ids": [],
            }, session_id="trial").content)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["time_h"], 12)
        self.assertFalse(result["applied"])

    def test_selected_forecast_does_not_follow_latest_pointer_or_playback(self):
        (self.root / "forecasts/latest.json").write_text(json.dumps({"forecast_id": "v099"}))
        self.source.index = 30
        plan = dispatch_trial.get_longtan_dispatch_plan("v001")
        result = self.trial()
        self.assertEqual(plan["t0"], self.t0)
        self.assertEqual(result["t0"], self.t0)
        self.assertEqual(result["baseline_forecast_id"], "v001")
        self.assertEqual(self.source.index, 30)

    def test_cannot_rewrite_initial_state_or_compare_missing_frame(self):
        with self.assertRaisesRegex(ValueError, "不能调整"):
            self.trial({"initial_level_m": 230})
        with self.assertRaisesRegex(ValueError, "精确切片"):
            dispatch_trial.simulate_longtan_dispatch(self.repo, {"mode": "RULE"}, "v001", 1.25)
        self.predict.assert_not_called()

    def test_missing_snapshot_is_not_rebuilt_from_latest(self):
        snapshot = deepcopy(self.snapshot)
        snapshot.pop("reservoir_dispatch_context")
        self.input_path.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "重新生成预测"):
            self.trial()
        self.predict.assert_not_called()

    def test_invalid_target_fails_before_model_and_scope_is_preserved(self):
        with self.assertRaises(ValueError):
            self.trial({"mode": "LEVEL", "target_level_m": -1})
        with self.assertRaises(ValueError):
            self.trial(object_ids=["missing"])
        self.predict.assert_not_called()
        result = self.trial(object_ids=[])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["comparison"]["selected_time"]["baseline"]["affected_count"], 0)
        self.assertFalse(result["candidate_satisfies_objective"])

    def test_model_failure_and_missing_output_never_mean_no_inundation(self):
        for response in ({"error": "测试失败"}, {"status": "completed"}):
            with self.subTest(response=response), patch.object(dispatch_trial, "run_cnn_v2_forecast", return_value=response):
                result = self.trial()
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("comparison", result)
                self.assertIsNone(result["candidate_satisfies_objective"])

    def test_actual_reservoir_levels_determine_safety(self):
        context = self.snapshot["reservoir_dispatch_context"]
        rows = deepcopy(context["baseline_series"][1:])
        rows[10]["end_level_m"] = 249
        result = dispatch_trial.reservoir_safety(self.t0, context["state"], rows)
        self.assertFalse(result["passed"])
        self.assertEqual(result["first_check_exceeded_at"], rows[10]["valid_time"])
        self.assertEqual(result["max_level_m"], 249)


if __name__ == "__main__":
    unittest.main()
