from __future__ import annotations

import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
