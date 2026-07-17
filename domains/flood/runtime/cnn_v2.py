from __future__ import annotations

import csv
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any

from .common import DOMAIN_DIR, GENERATED_DIR, PROJECT_DIR, rel


MODEL_DIR = DOMAIN_DIR / "model" / "cnn_v2"
MODEL_SCRIPT = MODEL_DIR / "CNN_V2.py"
GRID_PATH = MODEL_DIR / "GT.txt"
WEIGHT_PATH = MODEL_DIR / "weights" / "FLOOD_CNN.pth"
RUN_DIR = GENERATED_DIR / "cnn_v2" / "latest"

BOUNDARY_FILENAMES = {
    "interval1": "校核后-cnn-区间1.csv",
    "interval2": "校核后-cnn-区间2.csv",
    "tonggu": "校核后-cnn-同古河.csv",
    "upstream": "校核后-cnn-坝址.csv",
}


def run_cnn_v2_forecast(boundary_flow: dict[str, Any],
                        target_depth_path: Path) -> dict[str, Any]:
    if not MODEL_SCRIPT.exists():
        return {"error": f"missing CNN_V2.py: {rel(MODEL_SCRIPT)}"}
    if not GRID_PATH.exists():
        return {"error": f"missing CNN grid file: {rel(GRID_PATH)}"}
    if not WEIGHT_PATH.exists():
        return {"error": f"missing CNN weight file: {rel(WEIGHT_PATH)}"}

    summary = (boundary_flow or {}).get("summary") or {}
    if not summary:
        return {"error": "missing boundary flow summary"}

    case_name = str(summary.get("boundary_flow_id") or "latest")
    test_dir = RUN_DIR / "TEST"
    case_dir = test_dir / case_name
    output_dir = RUN_DIR / "OUTPUT"
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)
    case_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_case_csvs(summary, case_dir)
    runtime_model_path = output_dir / "FLOOD_CNN.pth"
    _link_or_copy(WEIGHT_PATH, runtime_model_path)

    command = [
        cnn_python(),
        str(MODEL_SCRIPT),
        "--mode", "predict",
        "--test-dir", str(test_dir),
        "--grid-file", str(GRID_PATH),
        "--model-path", str(runtime_model_path),
        "--no-timeseries-csv",
    ]
    env = dict(os.environ)
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    env.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    try:
        completed = subprocess.run(
            command,
            cwd=str(MODEL_DIR),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=int(env.get("FLOOD_CNN_TIMEOUT_SECONDS", "300")),
        )
    except FileNotFoundError as exc:
        return {
            "error": f"CNN python not found: {command[0]}",
            "detail": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "error": "CNN_V2 prediction timed out",
            "detail": str(exc),
        }
    if completed.returncode != 0:
        return {
            "error": "CNN_V2 prediction failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "python": command[0],
        }

    output_depth_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_max_depth.csv"
    if not output_depth_path.exists():
        return {
            "error": "CNN_V2 prediction did not produce max_depth.csv",
            "expected_path": rel(output_depth_path),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    target_depth_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output_depth_path, target_depth_path)
    stats = depth_csv_stats(target_depth_path)
    return {
        "status": "completed",
        "model_name": "FLOOD_CNN_V2",
        "model_description": "CNN_V2 水动力模型：四边界流量历史序列驱动，输出水动力网格 max_depth。",
        "case_name": case_name,
        "test_dir": rel(test_dir),
        "output_dir": rel(output_dir),
        "raw_output_depth_path": rel(output_depth_path),
        "hydrodynamic_depth_path": rel(target_depth_path),
        "python": command[0],
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        **stats,
    }


def cnn_python() -> str:
    configured = os.environ.get("FLOOD_CNN_PYTHON")
    if configured:
        return configured
    local_python = PROJECT_DIR / "local" / "hydrodynamic" / ".venv" / "bin" / "python"
    if local_python.exists():
        return str(local_python)
    return sys.executable


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _write_case_csvs(summary: dict[str, Any], case_dir: Path) -> None:
    boundaries = summary.get("boundaries") or {}
    for boundary_key, filename in BOUNDARY_FILENAMES.items():
        item = boundaries.get(boundary_key) or {}
        rows = item.get("series") or []
        path = case_dir / filename
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=["time_h", "flow_m3s"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "time_h": row.get("time_h", 0),
                    "flow_m3s": row.get("flow_m3s", 0),
                })


def depth_csv_stats(path: Path) -> dict[str, Any]:
    depth_count = 0
    flooded_count = 0
    max_depth = 0.0
    depth_sum = 0.0
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            depth = float(row.get("max_depth") or row.get("max_depth_m") or 0)
            depth_count += 1
            if depth > 0:
                flooded_count += 1
                depth_sum += depth
                max_depth = max(max_depth, depth)
    return {
        "depth_count": depth_count,
        "flooded_count": flooded_count,
        "max_depth_m": round(max_depth, 4),
        "mean_depth_m": round(depth_sum / flooded_count, 4) if flooded_count else 0.0,
    }
