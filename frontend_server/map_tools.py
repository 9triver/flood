from __future__ import annotations

import json
from typing import Any

from oag.tools.registry import ToolDef, ToolPolicy, ToolRegistry

from domains.flood.runtime.common import MAPPABLE_OBJECTS
from frontend_server.map_planner import OBJECT_LABELS


UI_TOOL_NAMES = {"ui_show_objects", "ui_clear_map", "ui_focus_object", "ui_show_event_marker"}


def register_map_tools(tools: ToolRegistry, resolver, registry) -> None:
    """Register frontend orchestration tools.

    These tools do not mutate domain data. They return declarative UI actions
    that the frontend service translates into SSE map_actions events.
    """

    object_types = sorted(MAPPABLE_OBJECTS)
    policy = ToolPolicy(
        read_only=False,
        requires_confirmation=False,
        concurrency_safe=False,
        worker_allowed=False,
        idempotent=False,
        destructive=False,
        timeout_seconds=5.0,
    )

    tools.register(ToolDef(
        name="ui_show_objects",
        description=(
            "在前端 GIS 地图中显示一个或多个领域对象。"
            "当用户要求显示、打开、绘制、加载、叠加、查看地图上的对象时必须调用。"
            "对象类型必须来自领域对象，不要使用图层名。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "object_type": {"type": "string", "enum": object_types},
                            "filters": {"type": "object", "description": "对象过滤条件，例如学校为 {\"facility_type\":\"school\"}"},
                            "label": {"type": "string", "description": "地图图层显示名称，可选"},
                            "fit": {"type": "boolean", "description": "是否缩放到该对象范围"},
                            "simplify_tolerance": {"type": "number", "description": "大型面对象简化容差，通常仅 Cell 使用"},
                        },
                        "required": ["object_type"],
                    },
                    "description": "要显示的领域对象列表。",
                },
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": ["objects"],
        },
        handler=lambda args: _show_objects(args, resolver, registry),
        usage_prompt=(
            "常用映射：珊瑚河/主河道/河道中心线 => River；河道/水系/河网 => Waterway；"
            "流域/小流域 => Watershed；行政边界 => County；危险区/风险点 => Risk；"
            "学校/医院/政府机构 => Facility 并分别过滤 facility_type=school/hospital/government；"
            "水文站/测站/雨量站/水位站/气象站 => HydroStation；"
            "水利设施/水利工程 => Reservoir、Sluice、HydraulicStructure；道路交通 => Road、Bridge；"
            "转移安置 => Transfer、Place、Route；淹没范围/洪水情景 => Cell，并传 scenario_id 或先调用 get_scenario_summary；"
            "预测淹没/未来淹没/实时预测 => ForecastCell，并传 forecast_id=latest 或先调用 run_flood_forecast。"
        ),
        category="ui",
        policy=policy,
        max_result_chars=8000,
    ))

    tools.register(ToolDef(
        name="ui_clear_map",
        description="清空或重置前端 GIS 地图显示。当用户说清空、重置、恢复基础图时调用。",
        parameters={
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": [],
        },
        handler=lambda args: _clear_map(args),
        category="ui",
        policy=policy,
        max_result_chars=2000,
    ))

    tools.register(ToolDef(
        name="ui_focus_object",
        description=(
            "缩放、打开弹窗并高亮前端地图中的某个领域对象。"
            "当用户要求定位某个水库、学校、道路、桥梁，或说这个/该对象并要求聚焦时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "description": "选中对象类型，可选"},
                "object_id": {"type": "string", "description": "选中对象 ID，可选"},
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": [],
        },
        handler=lambda args: _focus_object(args),
        category="ui",
        policy=policy,
        max_result_chars=2000,
    ))

    tools.register(ToolDef(
        name="ui_show_event_marker",
        description=(
            "在前端 GIS 地图中显示一个领域事件 marker。"
            "当后台水文异常、淹没异常、告警事件需要被用户在地图上直接感知时调用。"
            "该工具只改变前端显示，不改变领域数据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "description": "领域事件对象，必须包含 event_id/event_type/title/longitude/latitude/payload 等可用字段。",
                },
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
                "fit": {"type": "boolean", "description": "是否动画缩放到该事件 marker"},
                "show_source": {"type": "boolean", "description": "是否同时加载事件来源对象，例如水文测站"},
            },
            "required": ["event"],
        },
        handler=lambda args: _show_event_marker(args),
        usage_prompt=(
            "HydroThresholdExceeded 这类水文异常事件通常应调用 ui_show_event_marker，"
            "让前端用 marker 标出异常发生位置；如果需要同时看到测站对象，可设置 show_source=true。"
        ),
        category="ui",
        policy=policy,
        max_result_chars=4000,
    ))


def tool_result_to_map_event(tool_name: str, result: str) -> dict[str, Any] | None:
    if tool_name not in UI_TOOL_NAMES:
        return None
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("kind") != "frontend_map_actions":
        return None
    return {
        "type": "map_actions",
        "context": payload.get("context"),
        "map_actions": payload.get("map_actions", []),
        "result_cards": payload.get("result_cards", []),
    }


def _show_objects(args: dict[str, Any], resolver, registry) -> str:
    requested = args.get("objects") or []
    if not isinstance(requested, list):
        return _error("objects must be an array")

    actions: list[dict[str, Any]] = []
    cards: list[dict[str, str]] = []
    for index, item in enumerate(requested):
        if not isinstance(item, dict):
            return _error("each objects item must be an object")
        object_type = str(item.get("object_type") or "")
        if object_type not in MAPPABLE_OBJECTS:
            return _error(f"unsupported mappable object_type: {object_type}")
        filters = item.get("filters") or {}
        if not isinstance(filters, dict):
            return _error(f"filters for {object_type} must be an object")
        if object_type == "Cell" and not filters.get("scenario_id") and not filters.get("return_period_year"):
            return _error("Cell requires filters.scenario_id or filters.return_period_year")
        if object_type == "ForecastCell" and not filters.get("forecast_id"):
            filters["forecast_id"] = "latest"

        label = str(item.get("label") or OBJECT_LABELS.get(object_type) or object_type)
        action = {
            "type": "load_object",
            "object_type": object_type,
            "label": label,
            "filters": filters,
            "fit": bool(item.get("fit")) if "fit" in item else index == 0,
        }
        if item.get("simplify_tolerance") is not None:
            action["simplify_tolerance"] = item.get("simplify_tolerance")
        actions.append(action)

        cards.append({
            "title": label,
            "value": str(_count_mappable(resolver, registry, object_type, filters)),
            "detail": f"{OBJECT_LABELS.get(object_type, object_type)} 对象已加入地图显示",
        })

    context = str(args.get("context") or _default_context(actions))
    note = str(args.get("note") or _default_note(actions))
    return _payload(context=context, actions=_dedupe_actions(actions), cards=cards, note=note)


def _clear_map(args: dict[str, Any]) -> str:
    context = str(args.get("context") or "基础态 · 领域对象地图")
    note = str(args.get("note") or "已重置地图显示。")
    return _payload(context=context, actions=[{"type": "reset"}], cards=[], note=note)


def _focus_object(args: dict[str, Any]) -> str:
    object_type = str(args.get("object_type") or "")
    object_id = str(args.get("object_id") or "")
    title = OBJECT_LABELS.get(object_type, object_type) if object_type else "选中对象"
    context = str(args.get("context") or "对象定位 · 珊瑚河流域")
    note = str(args.get("note") or "已聚焦到当前选中对象。")
    action = {"type": "focus_object"}
    if object_type:
        action["object_type"] = object_type
    if object_id:
        action["object_id"] = object_id
    cards = [{
        "title": title,
        "value": object_id or "当前选中对象",
        "detail": "前端将定位、打开并高亮该对象",
    }]
    return _payload(context=context, actions=[action], cards=cards, note=note)


def _show_event_marker(args: dict[str, Any]) -> str:
    event = args.get("event") or {}
    if not isinstance(event, dict):
        return _error("event must be an object")
    actions: list[dict[str, Any]] = []
    if args.get("show_source") and event.get("source_type") in MAPPABLE_OBJECTS:
        actions.append({
            "type": "load_object",
            "object_type": event.get("source_type"),
            "label": OBJECT_LABELS.get(str(event.get("source_type")), str(event.get("source_type"))),
            "filters": {},
            "fit": False,
        })
    actions.append({
        "type": "show_event_marker",
        "event": event,
        "fit": bool(args.get("fit")) if "fit" in args else True,
    })
    context = str(args.get("context") or "事件告警 · 珊瑚河流域")
    note = str(args.get("note") or "已在地图上显示事件 marker。")
    payload = event.get("payload") or {}
    cards = [{
        "title": str(event.get("title") or event.get("event_type") or "领域事件"),
        "value": str(payload.get("value") or event.get("severity") or ""),
        "detail": str(payload.get("station_name") or event.get("source_id") or ""),
    }]
    return _payload(context=context, actions=actions, cards=cards, note=note)


def _payload(*, context: str, actions: list[dict[str, Any]],
             cards: list[dict[str, str]], note: str) -> str:
    return json.dumps({
        "kind": "frontend_map_actions",
        "context": context,
        "map_actions": actions,
        "result_cards": cards,
        "note": note,
    }, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _count_mappable(resolver, registry, object_type: str, filters: dict[str, Any]) -> int:
    if object_type == "Cell":
        return int(resolver.count("Cell", filters))
    if object_type == "ForecastCell":
        return int(resolver.count("ForecastCell", filters or {"forecast_id": "latest"}))
    return int(resolver.count(object_type, filters))


def _default_context(actions: list[dict[str, Any]]) -> str:
    types = {action.get("object_type") for action in actions}
    if "ForecastCell" in types:
        return "实时预测 · 珊瑚河流域"
    if "Cell" in types:
        return "洪水影响分析 · 珊瑚河流域"
    if types & {"Reservoir", "Sluice", "HydraulicStructure"}:
        return "水利工程设施 · 珊瑚河流域"
    if types & {"Road", "Bridge"}:
        return "交通基础设施 · 珊瑚河流域"
    return "对象分析 · 珊瑚河流域"


def _default_note(actions: list[dict[str, Any]]) -> str:
    labels = [str(action.get("label") or action.get("object_type")) for action in actions]
    return f"已在地图上显示：{'、'.join(labels)}。" if labels else "地图动作已执行。"


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for action in actions:
        key = (
            action.get("type"),
            action.get("object_type"),
            json.dumps(action.get("filters", {}), sort_keys=True, ensure_ascii=False),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result
