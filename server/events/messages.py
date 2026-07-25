from __future__ import annotations

from typing import Any


def readable_event_tool(name: str) -> str:
    return {
        "run_flood_forecast": "运行水动力模型",
        "run_emergency_cycle": "运行预警调度闭环",
        "analyze_inundation_impacts": "分析淹没影响对象",
        "ui_show_objects": "地图展示对象",
        "ui_show_event_marker": "地图展示事件",
        "ui_focus_object": "地图聚焦对象",
        "ui_clear_map": "清空地图",
    }.get(name, name or "tool")


def compact_event_text(value: Any, limit: int = 360) -> str:
    text = str(value or "")
    return f"{text[:limit]}..." if len(text) > limit else text


def is_impact_result(value: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(value, dict)
        and "error" not in value
        and "summary" in value
        and "impacts" in value
    )


def boundary_flow_observation_detail(observation: dict[str, Any]) -> str:
    boundaries = observation.get("boundaries") or {}
    parts = []
    for key in ("interval1", "interval2", "tonggu", "upstream"):
        item = boundaries.get(key) or {}
        if item:
            parts.append(
                f"{item.get('label', key)} "
                f"{format_float(item.get('flow_m3s'), 2)} m³/s"
            )
    return f"{observation.get('observed_at', '')}: " + "，".join(parts)


def boundary_flow_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return boundary_flow_observation_detail(payload.get("observation") or {})


def domain_event_detail(event: dict[str, Any]) -> str:
    if event.get("event_type") == "FloodForecastRequired":
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        return f"{boundary_flow_event_detail(event)}；{trigger.get('reason', '')}"
    if event.get("event_type") == "FloodEpisodeEnded":
        payload = event.get("payload") or {}
        return (
            f"{payload.get('ended_at', '')}，共生成 "
            f"{payload.get('forecast_versions', 0)} 个预测输入版本"
        )
    if event.get("event_type") == "InundationGenerated":
        return inundation_event_detail(event)
    if event.get("event_type") == "ImpactAnalyzed":
        return impact_event_detail(event)
    return str(event.get("severity") or "")


def inundation_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    area = format_float(payload.get("inundated_area_km2"), 2)
    depth = format_float(payload.get("max_depth_m"), 2)
    return (
        f"{payload.get('name') or payload.get('forecast_id')}: "
        f"预测单元 {payload.get('forecast_cell_count', 0)} 个，"
        f"淹没面积 {area} km²，最大水深 {depth} m"
    )


def impact_event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    summary = payload.get("summary") or {}
    labels = {
        "Facility": "设施",
        "Bridge": "桥梁",
        "Road": "道路",
        "Route": "路线",
        "Transfer": "转移单元",
        "Place": "安置点",
    }
    parts = []
    for key in ("Facility", "Bridge", "Road", "Route", "Transfer", "Place"):
        item = summary.get(key) or {}
        count = int(item.get("count") or 0)
        if count:
            parts.append(f"{labels[key]} {count} 个")
    return "，".join(parts) if parts else "未识别到受预测淹没影响的对象"


def format_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"
