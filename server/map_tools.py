from __future__ import annotations

import json
from typing import Any

from oag.tools.registry import ToolDef, ToolPolicy, ToolRegistry

from domains.flood.runtime.common import MAPPABLE_OBJECTS
from domains.flood.runtime.hydrodynamic_grid import hydrodynamic_grid_stats


OBJECT_LABELS = {
    "River": "珊瑚河",
    "Watershed": "珊瑚河流域",
    "Waterway": "河道水系",
    "HydrodynamicBoundary": "水动力边界",
    "County": "行政边界",
    "Town": "乡镇边界",
    "Road": "道路",
    "Reservoir": "水库",
    "Sluice": "水闸",
    "Bridge": "桥梁",
    "Facility": "重要设施",
    "HydraulicStructure": "水利工程",
    "Place": "安置地点",
    "Transfer": "转移对象",
    "Route": "转移路线",
    "Risk": "危险区",
    "HydroStation": "水文测站",
    "HistoricalFloodMark": "历史洪痕",
    "Cell": "淹没范围",
    "ForecastCell": "预测淹没",
    "HydrodynamicCell": "水动力网格",
}


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
                            "simplify_tolerance": {"type": "number", "description": "大型面对象简化容差，通常仅旧 Cell GeoJSON 使用"},
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
            "水动力边界/模型边界/入流边界/断面位置/河口水位/坝址边界 => HydrodynamicBoundary；"
            "流域/小流域 => Watershed；行政边界 => County；危险区/风险点 => Risk；"
            "乡镇/乡镇边界/镇界 => Town；"
            "学校/医院/政府机构 => Facility 并分别过滤 facility_type=school/hospital/government；"
            "水文站/测站/雨量站/水位站/气象站 => HydroStation；"
            "水利设施/水利工程 => Reservoir、Sluice、HydraulicStructure；道路交通 => Road、Bridge；"
            "转移安置 => Transfer、Place、Route；设计洪水/年一遇/洪水情景淹没范围 => HydrodynamicCell，并传 scenario_id 或 return_period_year；"
            "预测淹没/未来淹没/实时预测 => 先调用 run_flood_forecast，再用 HydrodynamicCell 并传 forecast_id=latest；"
            "水动力网格/模型网格/全部 cell/GT.txt 网格 => HydrodynamicCell。"
        ),
        category="ui",
        policy=policy,
        max_result_chars=8000,
    ))

    tools.register(ToolDef(
        name="ui_clear_map",
        description=(
            "清空或重置前端 GIS 地图显示。"
            "当用户只要求清除、不显示、隐藏淹没范围或预测结果时，target 必须传 inundation。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["map", "inundation"],
                    "description": "map 表示重置地图；inundation 表示只清除淹没范围/水动力结果，不改变地图视野",
                },
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
        label = str(item.get("label") or _default_object_label(object_type, filters) or object_type)
        fit = bool(item.get("fit")) if "fit" in item else index == 0
        if is_hydrodynamic_result_request(object_type, filters):
            result_filters = hydrodynamic_result_filters(object_type, filters)
            actions.append({"type": "show_hydrodynamic_mesh", "fit": False})
            actions.append({
                "type": "apply_hydrodynamic_result",
                "filters": result_filters,
                "label": label,
                "fit": False,
                "refresh": bool(item.get("refresh", True)),
            })
            object_type = "HydrodynamicCell"
            filters = result_filters
        elif object_type == "HydrodynamicCell":
            actions.append({"type": "show_hydrodynamic_mesh", "fit": fit, "mesh_only": True})
            filters = {"result": "mesh"}
        else:
            action = {
                "type": "load_object",
                "object_type": object_type,
                "label": label,
                "filters": filters,
                "fit": fit,
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
    target = str(args.get("target") or "map")
    if target == "inundation":
        context = str(args.get("context") or "淹没结果 · 已隐藏")
        note = str(args.get("note") or "已隐藏淹没范围。")
        return _payload(context=context, actions=[{"type": "clear_hydrodynamic_result"}], cards=[], note=note)
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
    if is_hydrodynamic_result_request(object_type, filters):
        result_id = _hydrodynamic_result_id(resolver, filters)
        stats = hydrodynamic_grid_stats(result_id)
        return int((stats.get("forecast") or {}).get("flooded_count") or stats.get("feature_count") or 0)
    if object_type == "HydrodynamicCell":
        stats = hydrodynamic_grid_stats("mesh")
        return int(stats.get("feature_count") or 0)
    if object_type == "ForecastCell":
        return int(resolver.count("ForecastCell", filters or {"forecast_id": "latest"}))
    return int(resolver.count(object_type, filters))


def _default_object_label(object_type: str, filters: dict[str, Any]) -> str:
    if object_type in {"HydrodynamicCell", "Cell", "ForecastCell"}:
        if filters.get("return_period_year"):
            return f"{filters['return_period_year']} 年一遇淹没范围"
        if filters.get("scenario_id"):
            return f"{filters['scenario_id']} 淹没范围"
        if object_type == "ForecastCell" or filters.get("forecast_id") == "latest":
            return "预测淹没结果"
        if filters.get("forecast_id") and filters.get("forecast_id") != "latest":
            return f"{filters['forecast_id']} 水动力结果"
    return OBJECT_LABELS.get(object_type, object_type)


def _hydrodynamic_result_id(resolver, filters: dict[str, Any]) -> str:
    if filters.get("scenario_id"):
        return str(filters["scenario_id"])
    if filters.get("return_period_year"):
        try:
            period = int(filters["return_period_year"])
        except (TypeError, ValueError):
            period = 0
        scenario = next((row for row in resolver.scenarios if row.get("return_period_year") == period), None)
        if scenario:
            return str(scenario.get("scenario_id") or "latest")
    return str(filters.get("forecast_id") or "latest")


def is_hydrodynamic_result_request(object_type: str, filters: dict[str, Any]) -> bool:
    return (
        object_type == "ForecastCell"
        or (object_type in {"HydrodynamicCell", "Cell"} and bool(
            filters.get("forecast_id") or filters.get("scenario_id") or filters.get("return_period_year")
        ))
    )


def hydrodynamic_result_filters(object_type: str, filters: dict[str, Any]) -> dict[str, Any]:
    if object_type == "ForecastCell" and not filters.get("forecast_id"):
        return {**filters, "forecast_id": "latest"}
    return dict(filters)


def _default_context(actions: list[dict[str, Any]]) -> str:
    types = {action.get("object_type") for action in actions}
    action_types = {action.get("type") for action in actions}
    if "apply_hydrodynamic_result" in action_types:
        return "淹没结果 · 珊瑚河流域"
    if "show_hydrodynamic_mesh" in action_types:
        return "水动力网格 · 珊瑚河流域"
    if "HydrodynamicCell" in types:
        return "水动力模型网格 · 珊瑚河流域"
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
    labels = [
        str(action.get("label") or action.get("object_type") or "水动力网格")
        for action in actions
    ]
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
