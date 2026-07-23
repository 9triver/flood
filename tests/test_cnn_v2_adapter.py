from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from domains.flood.runtime import cnn_v2
from domains.flood.runtime.cnn_v2 import BOUNDARY_FILES, _write_case_csvs
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope


class CnnV2AdapterTest(unittest.TestCase):
    def test_boundary_csvs_use_explicit_feature_order(self):
        boundaries = {
            key: {
                "series": [
                    {"time_h": 0, "flow_m3s": index + 1},
                    {"time_h": 1, "flow_m3s": index + 11},
                ],
            }
            for index, (key, _) in enumerate(BOUNDARY_FILES)
        }

        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory)
            _write_case_csvs({"boundaries": boundaries}, case_dir)

            paths = sorted(case_dir.glob("*.csv"))
            self.assertEqual(
                [filename for _, filename in BOUNDARY_FILES],
                [path.name for path in paths],
            )
            for index, path in enumerate(paths):
                with path.open(newline="", encoding="utf-8") as file:
                    rows = list(csv.DictReader(file))
                self.assertEqual(float(rows[0]["flow_m3s"]), index + 1)
                self.assertEqual(float(rows[1]["flow_m3s"]), index + 11)

    def test_success_keeps_only_canonical_forecast_outputs(self):
        boundaries = {
            key: {
                "series": [
                    {"time_h": 0, "flow_m3s": index + 1},
                    {"time_h": 1, "flow_m3s": index + 2},
                ],
            }
            for index, (key, _) in enumerate(BOUNDARY_FILES)
        }
        boundary_flow = {
            "summary": {
                "boundary_flow_id": "test_case",
                "boundaries": boundaries,
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            target = manager.path(workspace_id) / "forecasts" / "latest" / "max_depth.csv"
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                output_dir = Path(command[command.index("--output-dir") + 1])
                case_dir = output_dir / "TEST_RESULTS" / "test_case"
                case_dir.mkdir(parents=True)
                (case_dir / "test_case_max_depth.csv").write_text(
                    "cell_id,max_depth\n1,0.4\n2,0.0\n",
                    encoding="utf-8",
                )
                (case_dir / "test_case_pred_depths.npy").write_bytes(b"test-series")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(workspace_id):
                    with patch("domains.flood.runtime.cnn_v2.subprocess.run", fake_run):
                        result = cnn_v2.run_cnn_v2_forecast(boundary_flow, target)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(target.exists())
            self.assertTrue(target.with_name("depth_series.npy").exists())
            self.assertTrue(target.with_name("time_steps.json").exists())
            self.assertFalse((manager.path(workspace_id) / "cnn_v2" / "latest").exists())
            command = commands[0]
            self.assertEqual(str(cnn_v2.WEIGHT_PATH), command[command.index("--model-path") + 1])
            self.assertIn("--no-timeseries-csv", command)


if __name__ == "__main__":
    unittest.main()
