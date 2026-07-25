from __future__ import annotations

import json
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


def summarize_event_tool_result(name: str, value: Any) -> str:
    parsed = _parse_json_value(value)
    if parsed is None:
        return _compact_plain_text(value)
    if isinstance(parsed, dict) and parsed.get("error"):
        return f"工具返回错误：{parsed.get('error')}"
    if name == "run_flood_forecast" and isinstance(parsed, dict):
        return _forecast_result_summary(parsed)
    if name == "analyze_inundation_impacts" and isinstance(parsed, dict):
        return _impact_result_summary(parsed)
    if (
        isinstance(parsed, dict)
        and parsed.get("kind") == "frontend_map_actions"
    ):
        return _map_result_summary(parsed)
    bounded = _bounded_json_value(parsed)
    return "```json\n" + json.dumps(
        bounded,
        ensure_ascii=False,
        indent=2,
    ) + "\n```"


def _forecast_result_summary(result: dict[str, Any]) -> str:
    forecast = result.get("forecast") or result
    if not isinstance(forecast, dict):
        return _compact_plain_text(result)
    lines = [
        f"- 运行状态：{forecast.get('status') or '--'}",
        f"- 预测编号：{forecast.get('forecast_id') or '--'}",
    ]
    if forecast.get("forecast_input_id"):
        lines.append(f"- 输入版本：{forecast['forecast_input_id']}")
    if forecast.get("model_name"):
        lines.append(f"- 水动力模型：{forecast['model_name']}")
    lines.extend([
        f"- 预测淹没单元：{int(forecast.get('forecast_cell_count') or 0)} 个",
        f"- 淹没面积：{format_float(forecast.get('inundated_area_km2'), 3)} km²",
        f"- 最大水深：{format_float(forecast.get('max_depth_m'), 3)} m",
    ])
    if forecast.get("time_step_count") is not None:
        lines.append(f"- 时间步：{int(forecast.get('time_step_count') or 0)} 个")
    return "\n".join(lines)


def _impact_result_summary(result: dict[str, Any]) -> str:
    lines = [
        f"- 运行状态：{result.get('status') or '--'}",
        f"- 预测编号：{result.get('forecast_id') or '--'}",
        f"- 分析时刻：{_analysis_time_label(result.get('time_h'))}",
        f"- 受影响对象：{int(result.get('total_impacts') or 0)} 个",
    ]
    summary = result.get("summary") or {}
    if not isinstance(summary, dict):
        return "\n".join(lines)
    rows = []
    for object_type in ("Facility", "Bridge", "Road", "Route", "Transfer", "Place"):
        item = summary.get(object_type) or {}
        count = int(item.get("count") or 0)
        if not count:
            continue
        risk_parts = [
            f"严重 {int(item.get('critical') or 0)}",
            f"高风险 {int(item.get('high') or 0)}",
            f"中风险 {int(item.get('medium') or 0)}",
            f"低风险 {int(item.get('low') or 0)}",
        ]
        rows.append(
            f"  - {_IMPACT_LABELS[object_type]}：{count} 个（"
            + "，".join(risk_parts)
            + f"；最大水深 {format_float(item.get('max_depth_m'), 3)} m）"
        )
    if rows:
        lines.append("- 分类汇总：")
        lines.extend(rows)
    else:
        lines.append("- 分类汇总：未识别到受预测淹没影响的对象")
    return "\n".join(lines)


def _map_result_summary(result: dict[str, Any]) -> str:
    actions = result.get("map_actions") or []
    descriptions = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "地图动作")
        object_type = str(action.get("object_type") or "")
        label = str(action.get("label") or "")
        description = " / ".join(
            item for item in (action_type, object_type, label) if item
        )
        if description:
            descriptions.append(description)
    lines = [f"- 前端地图动作：{len(actions)} 项"]
    if result.get("context"):
        lines.append(f"- 地图上下文：{result['context']}")
    lines.extend(f"  - {description}" for description in descriptions[:12])
    if len(descriptions) > 12:
        lines.append(f"  - 另有 {len(descriptions) - 12} 项动作未展开")
    return "\n".join(lines)


def _parse_json_value(value: Any) -> Any | None:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return None


def _bounded_json_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, dict):
        if depth >= 3:
            return {"_summary": f"对象含 {len(value)} 个字段，未展开"}
        limit = 8 if depth == 0 else 5
        items = list(value.items())
        bounded = {
            str(key): _bounded_json_value(item, depth + 1)
            for key, item in items[:limit]
        }
        if len(items) > limit:
            bounded["_omitted_fields"] = len(items) - limit
        return bounded
    if isinstance(value, list):
        if depth >= 3:
            return [f"共 {len(value)} 项，未展开"]
        limit = 5
        bounded = [
            _bounded_json_value(item, depth + 1)
            for item in value[:limit]
        ]
        if len(value) > limit:
            bounded.append({"_omitted_items": len(value) - limit})
        return bounded
    if isinstance(value, str) and len(value) > 240:
        return f"{value[:240]}…（省略 {len(value) - 240} 字符）"
    return value


def _compact_plain_text(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n（另有 {len(text) - limit} 字符未展示）"


def _analysis_time_label(value: Any) -> str:
    if value in (None, ""):
        return "最大水深包络"
    return f"{format_float(value, 2)} h"


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
    parts = []
    for key in ("Facility", "Bridge", "Road", "Route", "Transfer", "Place"):
        item = summary.get(key) or {}
        count = int(item.get("count") or 0)
        if count:
            parts.append(f"{_IMPACT_LABELS[key]} {count} 个")
    return "，".join(parts) if parts else "未识别到受预测淹没影响的对象"


def format_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"


_IMPACT_LABELS = {
    "Facility": "设施",
    "Bridge": "桥梁",
    "Road": "道路",
    "Route": "路线",
    "Transfer": "转移单元",
    "Place": "安置点",
}
