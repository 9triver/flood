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


def forecast_result_context_error(
    source_event: dict[str, Any],
    result: dict[str, Any] | None,
) -> str:
    """Reject a completed forecast that belongs to another event context."""
    forecast = result.get("forecast") if isinstance(result, dict) else None
    if not isinstance(forecast, dict):
        return ""

    expected_workspace = str(source_event.get("workspace_id") or "")
    actual_workspace = str(forecast.get("workspace_id") or "")
    if expected_workspace and actual_workspace != expected_workspace:
        return (
            "预测结果所属演进不一致："
            f"当前为 {expected_workspace}，结果为 {actual_workspace or '未标注'}。"
        )

    expected_input = event_forecast_input_id(source_event)
    actual_input = str(forecast.get("forecast_input_id") or "")
    if expected_input and actual_input != expected_input:
        return (
            "预测结果输入版本不一致："
            f"当前为 {expected_input}，结果为 {actual_input or '未标注'}。"
        )
    return ""


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


def make_directive_issued_event(
    directive: dict[str, Any],
) -> dict[str, Any]:
    priority = str(directive.get("priority") or "normal")
    severity = {
        "critical": "critical",
        "urgent": "warning",
    }.get(priority, "info")
    return {
        "type": "domain_event",
        "event_id": f"evt_{uuid.uuid4().hex[:10]}",
        "event_type": "DirectiveIssued",
        "source_type": "EmergencyDirective",
        "source_id": str(directive.get("directive_id") or ""),
        "time": directive.get("issued_at") or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
        "severity": severity,
        "title": "应急指令已确认发出",
        "payload": directive,
        "correlation_id": str(directive.get("workspace_id") or ""),
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
