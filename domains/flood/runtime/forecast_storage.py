"""File layout and small persistence primitives for forecast runs.

The forecast service still owns the business workflow.  This module only
knows where a workspace stores forecast artifacts and how JSONL records are
read and written, which keeps storage details out of model orchestration.
"""

from __future__ import annotations

import json
from pathlib import Path

from .workspace import workspace_dir


def forecast_dir(*, create: bool = False) -> Path:
    return workspace_dir(create=create) / "forecasts" / "latest"


def forecast_runs_path() -> Path:
    return workspace_dir() / "forecasts" / "forecast_runs.jsonl"


def legacy_forecast_runs_path() -> Path:
    return forecast_dir() / "forecast_runs.jsonl"


def forecast_pointer_path() -> Path:
    return workspace_dir() / "forecasts" / "latest.json"


def forecast_cycle_path() -> Path:
    return forecast_dir() / "emergency_cycle.json"


def hydrodynamic_forecast_depth_path() -> Path:
    return forecast_dir() / "max_depth.csv"


def hydrodynamic_forecast_series_path() -> Path:
    return forecast_dir() / "depth_series.npy"


def hydrodynamic_forecast_time_steps_path() -> Path:
    return forecast_dir() / "time_steps.json"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True)
        for row in rows
    )
    path.write_text(f"{body}\n" if body else "", encoding="utf-8")


def read_forecast_runs() -> list[dict]:
    rows = read_jsonl(forecast_runs_path())
    return rows or read_jsonl(legacy_forecast_runs_path())


__all__ = [
    "forecast_dir",
    "forecast_runs_path",
    "legacy_forecast_runs_path",
    "forecast_pointer_path",
    "forecast_cycle_path",
    "hydrodynamic_forecast_depth_path",
    "hydrodynamic_forecast_series_path",
    "hydrodynamic_forecast_time_steps_path",
    "read_forecast_runs",
    "read_jsonl",
    "write_jsonl",
]
