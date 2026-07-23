from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
