from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from domains.flood.runtime import cnn_v2
from domains.flood.runtime.cnn_v2 import BOUNDARY_FILES, _write_case_csvs
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope


class CnnV2AdapterTest(unittest.TestCase):
    def tearDown(self):
        cnn_v2._CNN_WORKER.close()

    def test_cnn_uses_service_python_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLOOD_CNN_PYTHON", None)
            self.assertEqual(sys.executable, cnn_v2.cnn_python())

    def test_cnn_python_can_be_overridden(self):
        with patch.dict(os.environ, {"FLOOD_CNN_PYTHON": "/opt/flood/python"}):
            self.assertEqual("/opt/flood/python", cnn_v2.cnn_python())

    def test_cnn_prefers_cpu_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLOOD_CNN_DEVICE", None)
            self.assertEqual("cpu", cnn_v2.cnn_device())

    def test_cnn_device_can_be_overridden_explicitly(self):
        for device in ("cuda", "auto"):
            with self.subTest(device=device):
                with patch.dict(os.environ, {"FLOOD_CNN_DEVICE": device}):
                    self.assertEqual(device, cnn_v2.cnn_device())

    def test_invalid_cnn_device_is_rejected(self):
        with patch.dict(os.environ, {"FLOOD_CNN_DEVICE": "metal"}):
            with self.assertRaisesRegex(ValueError, "FLOOD_CNN_DEVICE"):
                cnn_v2.cnn_device()

    def test_persistent_worker_is_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLOOD_CNN_PERSISTENT_WORKER", None)
            self.assertTrue(cnn_v2.cnn_worker_enabled())

    def test_persistent_worker_can_be_disabled(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"FLOOD_CNN_PERSISTENT_WORKER": value}):
                    self.assertFalse(cnn_v2.cnn_worker_enabled())

    def test_inference_batch_size_is_configurable(self):
        with patch.dict(os.environ, {"FLOOD_CNN_BATCH_SIZE": "16"}):
            self.assertEqual(16, cnn_v2.cnn_inference_batch_size())
        for value in ("0", "invalid"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"FLOOD_CNN_BATCH_SIZE": value}):
                    with self.assertRaisesRegex(ValueError, "FLOOD_CNN_BATCH_SIZE"):
                        cnn_v2.cnn_inference_batch_size()

    def test_boundary_csvs_use_explicit_feature_order(self):
        boundaries = {
            key: {
                "series": [
                    {"time_h": time_h, "flow_m3s": index + time_h}
                    for time_h in range(25)
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
                self.assertEqual(len(rows), 25)
                self.assertEqual(
                    [float(row["time_h"]) for row in rows],
                    list(range(25)),
                )
                self.assertEqual(float(rows[0]["flow_m3s"]), index)
                self.assertEqual(float(rows[-1]["flow_m3s"]), index + 24)

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
                "boundary_flow_id": "water.flood.forecast-input/test/run",
                "boundaries": boundaries,
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory) / "workspaces")
            workspace_id = manager.create()["workspace_id"]
            target = manager.path(workspace_id) / "forecasts" / "latest" / "max_depth.csv"
            commands = []
            case_names = []

            def fake_run(command, **kwargs):
                commands.append(command)
                test_dir = Path(command[command.index("--test-dir") + 1])
                case_name = next(path.name for path in test_dir.iterdir() if path.is_dir())
                case_names.append(case_name)
                output_dir = Path(command[command.index("--output-dir") + 1])
                case_dir = output_dir / "TEST_RESULTS" / case_name
                case_dir.mkdir(parents=True)
                (case_dir / f"{case_name}_max_depth.csv").write_text(
                    "cell_id,max_depth\n1,0.4\n2,0.0\n",
                    encoding="utf-8",
                )
                (case_dir / f"{case_name}_pred_depths.npy").write_bytes(b"test-series")
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch.dict(os.environ, {"FLOOD_CNN_PERSISTENT_WORKER": "false"}):
                with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                    with workspace_scope(workspace_id):
                        with patch("domains.flood.runtime.cnn_v2.subprocess.run", fake_run):
                            result = cnn_v2.run_cnn_v2_forecast(boundary_flow, target)

            self.assertEqual(result["status"], "completed")
            self.assertTrue(target.exists())
            self.assertTrue(target.with_name("depth_series.npy").exists())
            self.assertTrue(target.with_name("time_steps.json").exists())
            self.assertFalse((manager.path(workspace_id) / "cnn_v2" / "latest").exists())
            self.assertEqual(1, len(case_names))
            self.assertNotIn("/", case_names[0])
            command = commands[0]
            self.assertEqual(str(cnn_v2.WEIGHT_PATH), command[command.index("--model-path") + 1])
            self.assertEqual(
                str(cnn_v2.GRID_CACHE_PATH),
                command[command.index("--grid-cache-file") + 1],
            )
            self.assertIn("--no-timeseries-csv", command)
            self.assertEqual("cpu", command[command.index("--device") + 1])
            self.assertEqual("8", command[command.index("--inference-batch-size") + 1])
            self.assertEqual("cpu", result["device"])
            self.assertFalse(result["persistent_worker"])
            self.assertEqual({1: 0.4}, result["_positive_depths"])

    def test_depth_csv_is_scanned_once_for_stats_and_positive_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max_depth.csv"
            path.write_text(
                "cell_id,max_depth\n1,0\n2,0.4\n3,invalid\n4,1.2\n",
                encoding="utf-8",
            )
            depths, stats = cnn_v2.read_depth_csv(path)

        self.assertEqual({2: 0.4, 4: 1.2}, depths)
        self.assertEqual(3, stats["depth_count"])
        self.assertEqual(2, stats["flooded_count"])
        self.assertEqual(1.2, stats["max_depth_m"])
        self.assertEqual(0.8, stats["mean_depth_m"])

    def test_persistent_worker_reuses_the_same_process(self):
        script = """
import json
import os
import sys

print(json.dumps({"type": "ready", "pid": os.getpid(), "device": "cpu"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "type": "result",
        "request_id": request["request_id"],
        "device": "cpu",
        "elapsed_ms": 1.0,
        "result": {"case": {"hours": 48}},
    }), flush=True)
"""
        with tempfile.TemporaryDirectory() as directory:
            worker_script = Path(directory) / "worker.py"
            worker_script.write_text(script, encoding="utf-8")
            worker = cnn_v2._CnnWorker()
            try:
                with patch.object(cnn_v2, "WORKER_SCRIPT", worker_script):
                    first = worker.run(
                        test_dir=Path(directory) / "test",
                        output_dir=Path(directory) / "output-1",
                        requested_device="cpu",
                        timeout=5,
                        env=dict(os.environ),
                    )
                    second = worker.run(
                        test_dir=Path(directory) / "test",
                        output_dir=Path(directory) / "output-2",
                        requested_device="cpu",
                        timeout=5,
                        env=dict(os.environ),
                    )
            finally:
                worker.close()

        self.assertFalse(first["worker_reused"])
        self.assertTrue(second["worker_reused"])
        self.assertEqual(first["worker_pid"], second["worker_pid"])
        self.assertEqual(48, second["result"]["case"]["hours"])


if __name__ == "__main__":
    unittest.main()
