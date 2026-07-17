from __future__ import annotations

import csv
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import DOMAIN_DIR, GENERATED_DIR, rel


MODEL_DIR = DOMAIN_DIR / "model" / "cnn_v2"
TRAIN_DIR = MODEL_DIR / "boundary_templates"
BOUNDARY_FLOW_DIR = GENERATED_DIR / "boundary_flows"
LATEST_BOUNDARY_FLOW_PATH = BOUNDARY_FLOW_DIR / "latest.json"

BOUNDARIES = {
    "interval1": "区间1",
    "interval2": "区间2",
    "tonggu": "同古河",
    "upstream": "坝址",
}

TRAINING_TEMPLATES = {
    "twenty_year": {
        "scenario_id": "45050092hsfx0001",
        "return_period_year": 20,
        "prefix": "校核后-20",
    },
    "ten_year": {
        "scenario_id": "45050092hsfx0002",
        "return_period_year": 10,
        "prefix": "校核后-10",
    },
    "five_year": {
        "scenario_id": "45050092hsfx0003",
        "return_period_year": 5,
        "prefix": "校核后-5",
    },
    "two_year": {
        "scenario_id": "45050092hsfx0004",
        "return_period_year": 2,
        "prefix": "校核后-2",
    },
    "check_flood": {
        "scenario_id": "45050092hsfx0005",
        "return_period_year": 1,
        "prefix": "校核后-1",
    },
}

DRY_PEAK_THRESHOLDS_M3S = {
    "interval1": 2.9,
    "interval2": 0.4,
    "tonggu": 5.4,
    "upstream": 3.6,
}


@dataclass(frozen=True)
class BoundaryFlowSample:
    mode: str
    template: str
    scale: float
    severity: str
    title: str


class BoundaryFlowMockService:
    """Mock boundary-flow adapter for development.

    It directly emits the four boundary flow series required by the
    hydrodynamic model. No station rainfall/water-level mock is involved.
    """

    def __init__(self):
        self._index = 0
        self._samples = [
            BoundaryFlowSample(
                mode="dry",
                template="twenty_year",
                scale=0.035,
                severity="normal",
                title="四边界低流量过程线",
            ),
            BoundaryFlowSample(
                mode="dry",
                template="twenty_year",
                scale=0.04,
                severity="normal",
                title="四边界平稳低流量过程线",
            ),
            BoundaryFlowSample(
                mode="rising",
                template="twenty_year",
                scale=0.72,
                severity="watch",
                title="四边界上涨流量过程线",
            ),
            BoundaryFlowSample(
                mode="flood",
                template="five_year",
                scale=0.9,
                severity="warning",
                title="四边界洪水流量过程线",
            ),
            BoundaryFlowSample(
                mode="flood",
                template="ten_year",
                scale=0.85,
                severity="warning",
                title="四边界强洪水流量过程线",
            ),
        ]

    def reset(self) -> None:
        self._index = 0

    def next_event(self) -> dict[str, Any]:
        sample = self._samples[self._index % len(self._samples)]
        self._index += 1
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        boundary_flow = generate_boundary_flow_series({
            "mode": sample.mode,
            "template": sample.template,
            "scale": sample.scale,
        })
        trigger = evaluate_forecast_trigger(boundary_flow)
        return {
            "event_id": event_id,
            "event_type": "BoundaryFlowSeriesGenerated",
            "source_type": "HydrodynamicBoundary",
            "source_id": boundary_flow["boundary_flow_id"],
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "severity": sample.severity,
            "title": sample.title,
            "payload": {
                "boundary_flow": boundary_flow["summary"],
                "forecast_trigger": trigger,
            },
            "correlation_id": f"corr_{event_id}",
        }


def generate_boundary_flow_series(observation: dict[str, Any]) -> dict[str, Any]:
    template_key = str(observation.get("template") or "twenty_year")
    scale = float(observation.get("scale") or 1.0)
    mode = str(observation.get("mode") or "flood")
    template = TRAINING_TEMPLATES.get(template_key, TRAINING_TEMPLATES["twenty_year"])
    series_id = f"boundary_flow_{uuid.uuid4().hex[:10]}"
    target_dir = BOUNDARY_FLOW_DIR / series_id
    target_dir.mkdir(parents=True, exist_ok=True)

    boundaries: dict[str, dict[str, Any]] = {}
    for boundary_key, label in BOUNDARIES.items():
        rows = load_template_rows(template, label)
        rows = transform_rows(rows, scale, mode, boundary_key)
        csv_path = target_dir / f"{boundary_key}.csv"
        write_flow_csv(csv_path, rows)
        peaks = [row["flow_m3s"] for row in rows]
        boundaries[boundary_key] = {
            "label": label,
            "csv_path": rel(csv_path),
            "point_count": len(rows),
            "series": [
                {
                    "time_h": round(row["time_s"] / 3600.0, 3),
                    "flow_m3s": round(row["flow_m3s"], 3),
                }
                for row in rows
            ],
            "peak_flow_m3s": round(max(peaks, default=0.0), 3),
            "mean_flow_m3s": round(sum(peaks) / len(peaks), 3) if peaks else 0.0,
            "first_flow_m3s": round(peaks[0], 3) if peaks else 0.0,
            "last_flow_m3s": round(peaks[-1], 3) if peaks else 0.0,
            "rising_ratio": round(rising_ratio(rows), 3),
        }

    summary = {
        "boundary_flow_id": series_id,
        "mode": mode,
        "template_key": template_key,
        "template_scenario_id": template["scenario_id"],
        "template_return_period_year": template["return_period_year"],
        "scale": round(scale, 4),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "boundaries": boundaries,
        "flow_index": round(flow_index(boundaries), 4),
        "data_dir": rel(target_dir),
    }
    result = {
        "boundary_flow_id": series_id,
        "summary": summary,
        "series_dir": str(target_dir),
    }
    (target_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LATEST_BOUNDARY_FLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_BOUNDARY_FLOW_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def evaluate_forecast_trigger(boundary_flow: dict[str, Any]) -> dict[str, Any]:
    summary = boundary_flow.get("summary") or boundary_flow
    boundaries = summary.get("boundaries") or {}
    exceeded = []
    for key, threshold in DRY_PEAK_THRESHOLDS_M3S.items():
        peak = float((boundaries.get(key) or {}).get("peak_flow_m3s") or 0)
        if peak >= threshold:
            exceeded.append({
                "boundary": key,
                "label": BOUNDARIES[key],
                "peak_flow_m3s": round(peak, 3),
                "threshold_m3s": threshold,
            })
    total_peak = sum(float((row or {}).get("peak_flow_m3s") or 0) for row in boundaries.values())
    max_rising_ratio = max((float((row or {}).get("rising_ratio") or 0) for row in boundaries.values()), default=0.0)
    should_run = bool(exceeded) and (total_peak >= 14.0 or max_rising_ratio >= 3.0)
    return {
        "should_run_forecast": should_run,
        "decision": "request_forecast" if should_run else "skip_dry_condition",
        "reason": (
            "边界流量超过干态门槛且过程线有上涨特征。"
            if should_run
            else "四边界峰值和上涨特征均不足以触发水动力模型。"
        ),
        "total_peak_flow_m3s": round(total_peak, 3),
        "max_rising_ratio": round(max_rising_ratio, 3),
        "exceeded_boundaries": exceeded,
    }


def read_latest_boundary_flow() -> dict[str, Any] | None:
    if not LATEST_BOUNDARY_FLOW_PATH.exists():
        return None
    try:
        return json.loads(LATEST_BOUNDARY_FLOW_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_template_rows(template: dict[str, Any], label: str) -> list[dict[str, float]]:
    path = TRAIN_DIR / template["scenario_id"] / f"{template['prefix']}-{label}.csv"
    if not path.exists():
        return [{"time_s": float(index * 3600), "flow_m3s": 0.0} for index in range(25)]
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows.append({
                "time_s": float(row.get("Time(s)") or row.get("time_s") or 0),
                "flow_m3s": float(row.get("Flow(m3/s)") or row.get("flow_m3s") or 0),
            })
    return rows


def transform_rows(rows: list[dict[str, float]], scale: float, mode: str,
                   boundary_key: str) -> list[dict[str, float]]:
    if mode == "dry":
        cap = DRY_PEAK_THRESHOLDS_M3S[boundary_key] * 0.72
        return [
            {
                "time_s": row["time_s"],
                "flow_m3s": round(min(row["flow_m3s"] * scale, cap), 6),
            }
            for row in rows
        ]
    if mode == "rising":
        midpoint = max(1, len(rows) // 2)
        result = []
        for index, row in enumerate(rows):
            ramp = 0.35 + 0.65 * min(1.0, index / midpoint)
            result.append({
                "time_s": row["time_s"],
                "flow_m3s": round(row["flow_m3s"] * scale * ramp, 6),
            })
        return result
    return [
        {
            "time_s": row["time_s"],
            "flow_m3s": round(row["flow_m3s"] * scale, 6),
        }
        for row in rows
    ]


def write_flow_csv(path: Path, rows: list[dict[str, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["time_s", "flow_m3s"])
        writer.writeheader()
        writer.writerows(rows)


def rising_ratio(rows: list[dict[str, float]]) -> float:
    if not rows:
        return 0.0
    first = max(float(rows[0]["flow_m3s"]), 0.01)
    peak = max(float(row["flow_m3s"]) for row in rows)
    return peak / first


def flow_index(boundaries: dict[str, dict[str, Any]]) -> float:
    if not boundaries:
        return 0.0
    ratios = []
    for key, threshold in DRY_PEAK_THRESHOLDS_M3S.items():
        peak = float((boundaries.get(key) or {}).get("peak_flow_m3s") or 0)
        ratios.append(peak / max(threshold, 1e-6))
    return max(0.0, min(8.0, math.sqrt(sum(ratios) / len(ratios))))
