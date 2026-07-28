from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import forecast
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope


class ForecastStorageTest(unittest.TestCase):
    def tearDown(self):
        forecast.clear_forecast_cell_cache()

    def test_forecast_cells_are_materialized_once_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            rows = [{
                "forecast_cell_id": "forecast_latest_1",
                "forecast_id": forecast.LATEST_FORECAST_ID,
                "depth_m": 0.4,
            }]
            depth_entry = {
                "depths": {1: 0.4},
                "stat_key": (1, 20),
                "time_h": 1.0,
            }

            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(workspace_id):
                    with patch.object(forecast, "ensure_latest_forecast"):
                        with patch.object(forecast, "forecast_depth_entry", return_value=depth_entry):
                            with patch.object(
                                forecast,
                                "forecast_cells_from_hydrodynamic_mesh",
                                return_value=rows,
                            ) as materialize:
                                first = forecast.query_forecast_cells(
                                    None,
                                    {"forecast_id": "latest", "time_h": 1.0},
                                )
                                second = forecast.query_forecast_cells(
                                    None,
                                    {"forecast_id": "latest", "time_h": 1.0},
                                )

            self.assertEqual(rows, first)
            self.assertEqual(rows, second)
            materialize.assert_called_once()
            self.assertFalse(
                (manager.path(workspace_id) / "forecasts" / "latest" / "forecast_cells.jsonl").exists()
            )

    def test_forecast_runs_are_versioned_while_latest_paths_remain_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            workspace_path = manager.path(workspace_id)
            latest_dir = workspace_path / "forecasts" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "max_depth.csv").write_text(
                "cell_id,max_depth\n1,0.4\n", encoding="utf-8",
            )
            (latest_dir / "depth_series.npy").write_bytes(b"series")
            (latest_dir / "time_steps.json").write_text(
                '{"time_steps_h":[0.5]}', encoding="utf-8",
            )
            base_run = {
                "schema_version": forecast.FORECAST_SCHEMA_VERSION,
                "forecast_id": forecast.LATEST_FORECAST_ID,
                "status": "completed",
                "generated_at": "2026-07-25T00:00:00+00:00",
                "boundary_flow": json.dumps({
                    "observed_through": "2025-01-01T08:00:00+08:00",
                    "window_start": "2025-01-01T03:00:00+08:00",
                    "window_end": "2025-01-02T03:00:00+08:00",
                    "rainfall_series": [{
                        "time_h": 0,
                        "valid_time": "2025-01-01T03:00:00+08:00",
                        "rainfall_mm": 2.5,
                    }],
                }),
                "forecast_trigger": json.dumps({"reason": "测试触发"}),
                "hydrodynamic_series_path": "latest/depth_series.npy",
            }

            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(workspace_id):
                    with patch.object(
                        forecast,
                        "generate_forecast",
                        side_effect=[base_run, {**base_run, "generated_at": "2026-07-25T01:00:00+00:00"}],
                    ):
                        first = forecast.ensure_latest_forecast(None, force=True)
                        second = forecast.ensure_latest_forecast(None, force=True)
                    latest = forecast.query_forecast_runs(
                        None, {"forecast_id": "latest"},
                    )

            self.assertEqual("v001", first["forecast_id"])
            self.assertEqual("v002", second["forecast_id"])
            self.assertEqual(
                [{
                    "time_h": 0,
                    "valid_time": "2025-01-01T03:00:00+08:00",
                    "rainfall_mm": 2.5,
                }],
                second["rainfall_series"],
            )
            self.assertEqual([second], latest)
            self.assertTrue((workspace_path / "forecasts" / "v001" / "max_depth.csv").exists())
            self.assertTrue((workspace_path / "forecasts" / "v002" / "max_depth.csv").exists())
            self.assertTrue((workspace_path / "forecasts" / "latest" / "max_depth.csv").exists())
            rows = forecast.read_jsonl(
                workspace_path / "forecasts" / "forecast_runs.jsonl"
            )
            self.assertEqual([False, True], [row["is_latest"] for row in rows])

    def test_query_does_not_implicitly_run_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(workspace_id):
                    with patch.object(forecast, "ensure_latest_forecast") as ensure:
                        self.assertEqual([], forecast.query_forecast_runs(None))
                        self.assertEqual([], forecast.query_forecast_cells(None))
            ensure.assert_not_called()

    def test_forecast_generation_is_serialized_per_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            workspace_path = manager.path(workspace_id)
            latest_dir = workspace_path / "forecasts" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "max_depth.csv").write_text(
                "cell_id,max_depth\n1,0.4\n", encoding="utf-8",
            )
            active_generations = 0
            max_active_generations = 0
            generation_count = 0
            count_lock = threading.Lock()

            def generate(_resolver):
                nonlocal active_generations, max_active_generations, generation_count
                with count_lock:
                    active_generations += 1
                    generation_count += 1
                    max_active_generations = max(
                        max_active_generations, active_generations,
                    )
                time.sleep(0.03)
                with count_lock:
                    active_generations -= 1
                return {
                    "schema_version": forecast.FORECAST_SCHEMA_VERSION,
                    "status": "completed",
                    "generated_at": "2026-07-25T00:00:00+00:00",
                    "boundary_flow": "{}",
                    "forecast_trigger": "{}",
                }

            def run_forecast():
                with workspace_scope(workspace_id):
                    return forecast.ensure_latest_forecast(None, force=True)

            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with patch.object(forecast, "generate_forecast", side_effect=generate):
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        runs = list(pool.map(lambda _: run_forecast(), range(4)))

            self.assertEqual(4, generation_count)
            self.assertEqual(1, max_active_generations)
            self.assertEqual(
                ["v001", "v002", "v003", "v004"],
                [run["forecast_id"] for run in runs],
            )


if __name__ == "__main__":
    unittest.main()
