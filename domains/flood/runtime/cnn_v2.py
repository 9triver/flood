from __future__ import annotations

import csv
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Any

from .common import DOMAIN_DIR, PROJECT_DIR, rel
from .workspace import workspace_dir


MODEL_DIR = DOMAIN_DIR / "model" / "cnn_v2"
MODEL_SCRIPT = MODEL_DIR / "CNN_V2.py"
GRID_PATH = MODEL_DIR / "GT.txt"
WEIGHT_PATH = MODEL_DIR / "weights" / "FLOOD_CNN.pth"

BOUNDARY_FILES = (
    ("interval1", "00_interval1.csv"),
    ("interval2", "01_interval2.csv"),
    ("tonggu", "02_tonggu.csv"),
    ("upstream", "03_upstream.csv"),
)


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
    run_dir = workspace_dir(create=True) / "cnn_v2" / "latest"
    test_dir = run_dir / "TEST"
    case_dir = test_dir / case_name
    output_dir = run_dir / "OUTPUT"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_case_csvs(summary, case_dir)

    command = [
        cnn_python(),
        str(MODEL_SCRIPT),
        "--mode", "predict",
        "--test-dir", str(test_dir),
        "--grid-file", str(GRID_PATH),
        "--model-path", str(WEIGHT_PATH),
        "--output-dir", str(output_dir),
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
    output_series_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_pred_depths.npy"
    output_time_series_csv_path = output_dir / "TEST_RESULTS" / case_name / f"{case_name}_time_series.csv"
    if not output_depth_path.exists():
        return {
            "error": "CNN_V2 prediction did not produce max_depth.csv",
            "expected_path": rel(output_depth_path),
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    target_depth_path.parent.mkdir(parents=True, exist_ok=True)
    target_series_path = target_depth_path.with_name("depth_series.npy")
    target_time_steps_path = target_depth_path.with_name("time_steps.json")
    time_steps = _read_time_steps(output_time_series_csv_path)
    if not time_steps:
        time_steps = _regular_time_steps(summary)
    _replace_file(output_depth_path, target_depth_path)
    for stale_path in (target_series_path, target_time_steps_path):
        if stale_path.exists():
            stale_path.unlink()
    if output_series_path.exists():
        _replace_file(output_series_path, target_series_path)
        target_time_steps_path.write_text(
            json.dumps({
                "time_steps_h": time_steps,
                "source": "FLOOD_CNN_V2 depth series",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    stats = depth_csv_stats(target_depth_path)
    result = {
        "status": "completed",
        "model_name": "FLOOD_CNN_V2",
        "model_description": "CNN_V2 水动力模型：四边界流量历史序列驱动，输出水动力网格多时刻水深与 max_depth。",
        "case_name": case_name,
        "hydrodynamic_depth_path": rel(target_depth_path),
        "hydrodynamic_series_path": rel(target_series_path) if target_series_path.exists() else "",
        "hydrodynamic_time_steps_path": rel(target_time_steps_path) if target_time_steps_path.exists() else "",
        "time_steps_h": time_steps,
        "time_step_count": len(time_steps),
        "python": command[0],
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        **stats,
    }
    shutil.rmtree(run_dir, ignore_errors=True)
    return result


def cnn_python() -> str:
    configured = os.environ.get("FLOOD_CNN_PYTHON")
    if configured:
        return configured
    local_python = PROJECT_DIR / "local" / "hydrodynamic" / ".venv" / "bin" / "python"
    if local_python.exists():
        return str(local_python)
    return sys.executable


def _replace_file(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    source.replace(target)


def _write_case_csvs(summary: dict[str, Any], case_dir: Path) -> None:
    boundaries = summary.get("boundaries") or {}
    for boundary_key, filename in BOUNDARY_FILES:
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


def _read_time_steps(path: Path) -> list[float]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as file:
        header = next(csv.reader(file), [])
    steps = []
    for value in header[1:]:
        if value.startswith("h_"):
            value = value[2:]
        try:
            steps.append(round(float(value), 4))
        except ValueError:
            continue
    return steps


def _regular_time_steps(summary: dict[str, Any]) -> list[float]:
    duration = 0.0
    for boundary in (summary.get("boundaries") or {}).values():
        for row in boundary.get("series") or []:
            duration = max(duration, float(row.get("time_h") or 0))
    interval = _read_output_interval_hours()
    if duration <= 0 or interval <= 0:
        return []
    count = int(duration // interval)
    steps = [round(index * interval, 4) for index in range(1, count + 1)]
    if not steps or abs(steps[-1] - duration) > 1e-6:
        steps.append(round(duration, 4))
    return steps


def _read_output_interval_hours() -> float:
    path = MODEL_DIR / "TIME.txt"
    if not path.exists():
        return 0.5
    with path.open(encoding="utf-8") as file:
        for line in file:
            value_part, _, key_part = line.partition("#")
            if key_part.strip().split()[:1] == ["output_interval_hours"]:
                try:
                    return float(value_part.strip())
                except ValueError:
                    return 0.5
    return 0.5


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
