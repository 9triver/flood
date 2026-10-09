from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from domains.flood.runtime import dispatch_trial, rainfall_scenario
from domains.flood.runtime.boundary_flow import BoundaryFlowPlaybackSource, FloodForecastPolicy, load_boundary_flow_rows
from domains.flood.runtime.rainfall_input import BASIN_RAINFALL_COLUMNS
from domains.flood.runtime.reservoir_dispatch import DispatchSettings
from domains.flood.runtime.workspace import WorkspaceManager
from tests.test_impact_analysis import StaticResolver, flood_cell


def write_prediction(boundary, folder):
    """Small deterministic model fixture; domain hydrology remains real."""
    steps = np.arange(0, 24.5, 0.5)
    forcing = boundary["summary"]["boundaries"]["interval1"]["series"]
    flows = np.interp(steps, [p["time_h"] for p in forcing], [p["flow_m3s"] for p in forcing])
    values = np.stack([flows / 100, np.maximum(flows - 90, 0) / 100], axis=1) * steps[:, None] / 24
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "depth_series.npy", values.astype(np.float32))
    (folder / "time_steps.json").write_text(json.dumps({"time_steps_h": steps.tolist()}))
    (folder / "max_depth.csv").write_text(f"cell_id,max_depth\n1,{values[:, 0].max()}\n2,{values[:, 1].max()}\n")


class RainfallScenarioTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.manager = WorkspaceManager(root / "workspaces")
        self.wid = self.manager.create()["workspace_id"]
        self.stack.enter_context(patch("domains.flood.runtime.workspace.WORKSPACES", self.manager))
        self.root = self.manager.path(self.wid)
        self.csv_path = self.root / "inputs/rainfall.csv"
        self.csv_path.parent.mkdir()
        with self.csv_path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["time_period_end", *BASIN_RAINFALL_COLUMNS.values(), "target_outflow_m3s"])
            for i in range(60):
                writer.writerow([(datetime(2026, 7, 1) + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M"),
                                 *([8, 4, 40] if 6 <= i <= 20 else [0, 0, 0]), 15 + i % 3])
        self.source = BoundaryFlowPlaybackSource(self.csv_path, self.root / "observations.jsonl",
                                                dispatch_settings=DispatchSettings(mode="RULE"))
        self.source.index = 5
        observation = self.source.next_observation()
        policy = FloodForecastPolicy(self.source.rows, total_trigger_m3s=1)
        policy.observe(observation)
        self.snapshot = policy.latest_forecast_input
        self.input_path = Path(self.snapshot["summary"]["input_path"])
        self.t0 = observation["observed_at"]
        self.manager.update_manifest(status="paused", simulation_time=self.t0)
        self.baseline_dir = self.root / "forecasts/v001"
        write_prediction(self.snapshot, self.baseline_dir)
        metadata = {"workspace_id": self.wid, "status": "completed", "forecast_id": "v001",
                    "forecast_input_id": self.snapshot["boundary_flow_id"], "valid_from": self.t0,
                    "valid_to": self.snapshot["summary"]["window_end"], "generated_at": self.t0,
                    "boundary_flow": json.dumps(self.snapshot["summary"])}
        (self.baseline_dir / "forecast.json").write_text(json.dumps(metadata))
        (self.root / "forecasts/latest.json").write_text(json.dumps({"forecast_id": "v001"}))
        self.original_files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.repo = StaticResolver({"Road": [
            {"road_id": f"r{i}", "name": f"X70{i}", "geometry_type": "LineString",
             "geometry": json.dumps({"type": "LineString", "coordinates": [[lon - .00005, 24.4], [lon + .00005, 24.4]]})}
            for i, lon in ((1, 111.3), (2, 111.31))
        ]})
        def cells(depths, generated_at, time_h, forecast_id):
            return [{**flood_cell(str(key), 111.3 + (key - 1) * .01, 24.4, value),
                     "forecast_id": forecast_id, "lead_time_h": time_h or 0} for key, value in depths.items()]
        self.stack.enter_context(patch.object(dispatch_trial, "forecast_cells_from_hydrodynamic_mesh", side_effect=cells))
        self.calls = []
        def predict(boundary, target, *, work_dir):
            self.assertTrue(work_dir.is_relative_to(self.root / "rainfall_scenarios"))
            self.calls.append(deepcopy(boundary))
            write_prediction(boundary, target.parent)
            return {"status": "completed"}
        self.predict = self.stack.enter_context(patch.object(rainfall_scenario, "run_cnn_v2_forecast", side_effect=predict))

    def scenario(self, multiplier=2, **kwargs):
        return rainfall_scenario.simulate_flood_scenario(self.repo, multiplier, "v001", time_h=12, **kwargs)

    def test_double_rainfall_recomputes_runoff_and_future_dispatch_without_changing_live_state(self):
        with patch.object(rainfall_scenario, "simulate_reservoir_dispatch",
                          wraps=rainfall_scenario.simulate_reservoir_dispatch) as dispatch:
            result = self.scenario()
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["applied"])
        self.assertEqual(result["t0"], self.t0)
        self.assertEqual(result["t1"], "2026-07-01T17:00:00+08:00")
        self.assertEqual(result["dispatch_settings"]["mode"], "RULE")
        for column in BASIN_RAINFALL_COLUMNS.values():
            self.assertAlmostEqual(result["rainfall_24h_mm"]["candidate"][column],
                                   result["rainfall_24h_mm"]["baseline"][column] * 2)
        for comparison in result["comparison"].values():
            self.assertTrue(comparison["complete"])
            self.assertEqual(comparison["baseline"]["affected_count"], 1)
            self.assertEqual(comparison["candidate"]["affected_count"], 2)
            self.assertEqual(comparison["new_count"], 1)
            self.assertGreater(comparison["inundation"]["candidate"]["max_depth_m"],
                               comparison["inundation"]["baseline"]["max_depth_m"])
        state = self.snapshot["reservoir_dispatch_context"]["state"]
        self.assertEqual(dispatch.call_args.kwargs["initial_storage_1e4m3"], state["storage_1e4m3"])
        self.assertEqual(dispatch.call_args.kwargs["initial_level_m"], state["level_m"])
        folder = self.root / "rainfall_scenarios" / result["scenario_id"]
        reservoir = json.loads((folder / "reservoir.json").read_text())
        self.assertAlmostEqual(reservoir["series"][0]["start_storage_1e4m3"], state["storage_1e4m3"], places=5)
        old = self.snapshot["summary"]["boundaries"]
        new = self.calls[0]["summary"]["boundaries"]
        for key in old:
            self.assertEqual(new[key]["series"][0], old[key]["series"][0])
        # Routing lag and baseflow mean even interval flow is not multiplied blindly.
        self.assertEqual(new["interval1"]["series"][1]["flow_m3s"], old["interval1"]["series"][1]["flow_m3s"])
        self.assertNotEqual(new["interval1"]["series"][12]["flow_m3s"], old["interval1"]["series"][12]["flow_m3s"] * 2)
        self.assertEqual(self.source.index, 6)
        for path, content in self.original_files.items():
            self.assertEqual(path.read_bytes(), content, path)
        self.assertTrue((folder / "baseline_input.json").exists())
        self.assertTrue((folder / "impact_details.json").exists())

    def test_multiplier_one_reproduces_original_boundaries_and_impacts(self):
        result = self.scenario(1)
        self.assertEqual(result["status"], "completed", result)
        for key in self.snapshot["summary"]["boundaries"]:
            old = [p["flow_m3s"] for p in self.snapshot["summary"]["boundaries"][key]["series"]]
            new = [p["flow_m3s"] for p in self.calls[0]["summary"]["boundaries"][key]["series"]]
            np.testing.assert_allclose(old, new, rtol=0, atol=1e-5)
        for comparison in result["comparison"].values():
            self.assertEqual(comparison["baseline"], comparison["candidate"])
            self.assertEqual(comparison["new_count"], 0)
            self.assertEqual(comparison["removed_count"], 0)

    def test_window_changes_only_selected_future_rainfall_and_preserves_targets_and_tail(self):
        result = self.scenario(2, from_time_h=4, to_time_h=9)
        self.assertEqual(result["status"], "completed", result)
        before = self.snapshot["rainfall_runoff_context"]["series"]
        after = self.calls[0]["rainfall_runoff_context"]["series"]
        for i, (old, new) in enumerate(zip(before, after)):
            factor = 2 if 5 + 4 < i <= 5 + 9 else 1
            for column in BASIN_RAINFALL_COLUMNS.values():
                self.assertEqual(new[column], old[column] * factor)
            self.assertEqual(new["target_outflow_m3s"], old["target_outflow_m3s"])
        csv_path = self.root / "rainfall_scenarios" / result["scenario_id"] / "rainfall.csv"
        # Saved input remains a valid replay CSV with the entire routing history.
        self.assertEqual(len(load_boundary_flow_rows(csv_path)), 60)

    def test_zero_rainfall_reduces_peak_instead_of_retaining_old_future_peak(self):
        with patch.object(rainfall_scenario, "simulate_reservoir_dispatch",
                          wraps=rainfall_scenario.simulate_reservoir_dispatch) as dispatch:
            result = self.scenario(0)
        self.assertEqual(result["status"], "completed", result)
        self.assertAlmostEqual(dispatch.call_args.kwargs["input_peak_inflow_m3s"], 0.2)
        self.assertGreater(self.snapshot["reservoir_dispatch_context"]["state"]["input_peak_inflow_m3s"], 1)
        self.assertEqual(result["comparison"]["window_envelope"]["candidate"]["affected_count"], 0)

    def test_snapshot_is_immutable_even_after_csv_latest_pointer_and_playback_change(self):
        self.csv_path.write_text("replaced upload")
        (self.root / "forecasts/latest.json").write_text(json.dumps({"forecast_id": "v099"}))
        self.source.index = 30
        result = self.scenario()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["baseline_forecast_id"], "v001")
        self.assertEqual(result["rainfall_source"], "forecast_input_snapshot")
        self.assertEqual(self.source.index, 30)
        self.assertEqual(self.csv_path.read_text(), "replaced upload")

    def test_old_forecast_accepts_only_reproducible_workspace_input(self):
        snapshot = deepcopy(self.snapshot)
        snapshot.pop("rainfall_runoff_context")
        self.input_path.write_text(json.dumps(snapshot))
        self.assertEqual(self.scenario()["rainfall_source"], "verified_workspace_csv")
        self.csv_path.write_text(self.csv_path.read_text().replace(",8,4,40,", ",16,8,80,"))
        with self.assertRaisesRegex(ValueError, "重新生成预测"):
            self.scenario()
        self.csv_path.unlink()
        with self.assertRaisesRegex(ValueError, "缺少降水输入快照"):
            self.scenario()
        self.assertEqual(self.predict.call_count, 1)

    def test_changed_model_and_unmatched_rainfall_are_rejected_before_prediction(self):
        for mutation in ("signature", "forcing"):
            snapshot = deepcopy(self.snapshot)
            if mutation == "signature":
                snapshot["rainfall_runoff_context"]["model_signature"] = "changed"
            else:
                snapshot["rainfall_runoff_context"]["series"][10]["reservoir_rainfall_mm"] = 999
            self.input_path.write_text(json.dumps(snapshot))
            with self.assertRaisesRegex(ValueError, "重新生成预测"):
                self.scenario()
        self.predict.assert_not_called()

    def test_invalid_assumptions_and_missing_frame_fail_before_prediction(self):
        for value in (-1, True, float("nan"), float("inf"), "abc", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.scenario(value)
        for start, end in ((-1, 24), (1, 1), (2, 1), (0, 25), (0.5, 12), (0, float("nan"))):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.scenario(from_time_h=start, to_time_h=end)
        with self.assertRaisesRegex(ValueError, "精确切片"):
            rainfall_scenario.simulate_flood_scenario(self.repo, 2, "v001", time_h=1.25)
        self.predict.assert_not_called()

    def test_scope_validation_and_empty_scope(self):
        with self.assertRaises(ValueError):
            self.scenario(object_ids=["missing"])
        self.predict.assert_not_called()
        result = self.scenario(object_ids=[])
        self.assertEqual(result["status"], "completed", result)
        for comparison in result["comparison"].values():
            self.assertEqual(comparison["analysis_scope"]["matched_count"], 0)
            self.assertEqual(comparison["candidate"]["affected_count"], 0)
            self.assertGreater(comparison["inundation"]["candidate"]["wet_cell_count"], 0)

    def test_failed_missing_or_misaligned_prediction_is_not_a_dry_result(self):
        def invalid(boundary, target, *, work_dir):
            write_prediction(boundary, target.parent)
            np.save(target.parent / "depth_series.npy", np.zeros((49, 3)))
            return {"status": "completed"}
        for response in ({"error": "计算失败"}, {"status": "completed"}, invalid):
            with self.subTest(response=response):
                self.predict.side_effect = response if callable(response) else None
                self.predict.return_value = response if not callable(response) else None
                result = self.scenario()
                self.assertEqual(result["status"], "failed", result)
                self.assertNotIn("comparison", result)
                self.assertFalse(result["applied"])
                saved = json.loads((self.root / "rainfall_scenarios" / result["scenario_id"] / "report.json").read_text())
                self.assertEqual(saved["status"], "failed")

    def test_registered_agent_tool_uses_frozen_map_frame_and_records_result(self):
        from oag.harness import Harness
        from oag.ontology.loader import load_domain
        from server.chat.analysis_context import analysis_scope, capture_analysis_context, normalize_analysis_tool
        from server.chat.side_effects import AgentSideEffects
        ontology, repo, registry = load_domain(Path(__file__).resolve().parents[1] / "domains/flood")
        harness = Harness(ontology, repo, registry, None, "test")
        harness.hooks.register("pre_tool_call", normalize_analysis_tool)
        side_effects = AgentSideEffects([])
        side_effects.begin_domain_results("scenario")
        harness.hooks.register("post_tool_call", side_effects.capture_tool_event)
        selection = {"workspace_id": self.wid, "hydrodynamic_timeline": {
            "active": True, "mode": "time_slice", "forecast_version": "v001", "current_hydrodynamic_time_h": 12,
        }}
        analysis = capture_analysis_context(selection)
        selection["hydrodynamic_timeline"]["current_hydrodynamic_time_h"] = 20
        with analysis_scope(analysis):
            result = json.loads(harness.execute_tool("simulate_flood_scenario", {
                "rainfall_multiplier": 2, "object_ids": [],
            }, session_id="scenario").content)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["time_h"], 12)
        self.assertEqual(result["baseline_forecast_id"], "v001")
        self.assertEqual(side_effects.pop_domain_results("scenario")[0]["result"]["scenario_id"], result["scenario_id"])
        tool = harness.tools.get("simulate_flood_scenario")
        self.assertFalse(tool.policy.worker_allowed)
        self.assertFalse(tool.policy.idempotent)
        self.assertFalse(tool.requires_confirmation)
        self.assertIn("rainfall_multiplier", tool.parameters["required"])
        for policy in ontology.event_policies.values():
            self.assertNotIn("simulate_flood_scenario", policy.allowed_tools)


if __name__ == "__main__":
    unittest.main()
