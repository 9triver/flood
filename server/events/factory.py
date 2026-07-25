from __future__ import annotations

import time
import uuid
from typing import Any


def event_forecast_input_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    forecast_input = payload.get("forecast_input") or {}
    return str(
        forecast_input.get("boundary_flow_id")
        or event.get("source_id")
        or ""
    )


def forecast_completed(result: dict[str, Any] | None) -> bool:
    forecast = result.get("forecast") if isinstance(result, dict) else None
    return (
        isinstance(forecast, dict)
        and str(forecast.get("status") or "") == "completed"
    )


def make_inundation_event(source_event: dict[str, Any],
                          forecast_result: dict[str, Any],
                          severity: str) -> dict[str, Any]:
    forecast = forecast_result.get("forecast") or {}
    forecast_input_id = (
        forecast.get("forecast_input_id")
        or event_forecast_input_id(source_event)
        or forecast.get("generated_at", "")
    )
    return {
        "type": "domain_event",
        "event_id": f"evt_{uuid.uuid4().hex[:10]}",
        "event_type": "InundationGenerated",
        "source_type": "HydrodynamicModel",
        "source_id": (
            f"{forecast.get('forecast_id', 'latest')}:"
            f"{forecast_input_id}"
        ),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "severity": severity,
        "title": "水动力模型生成预测淹没范围",
        "payload": forecast,
        "correlation_id": source_event["correlation_id"],
    }


def make_impact_event(impact_result: dict[str, Any],
                      session_id: str) -> dict[str, Any]:
    return {
        "type": "domain_event",
        "event_id": f"evt_{uuid.uuid4().hex[:10]}",
        "event_type": "ImpactAnalyzed",
        "source_type": "OntologyFunction",
        "source_id": str(impact_result.get("forecast_id") or "latest"),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "severity": impact_event_severity(impact_result),
        "title": "预测淹没影响对象分析完成",
        "payload": impact_result,
        "correlation_id": session_id,
    }


def impact_event_severity(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    critical = sum(
        int((item or {}).get("critical") or 0)
        for item in summary.values()
    )
    high = sum(
        int((item or {}).get("high") or 0)
        for item in summary.values()
    )
    if critical:
        return "critical"
    if high:
        return "warning"
    if int(result.get("total_impacts") or 0):
        return "info"
    return "normal"
