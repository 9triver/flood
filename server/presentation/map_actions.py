from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Collection

from oag.ontology.schema import Ontology

from domains.flood.runtime.common import MAPPABLE_OBJECTS
from server.presentation.hydrodynamic import (
    build_hydrodynamic_action_plan,
    count_hydrodynamic,
    default_hydrodynamic_label,
)
from server.presentation.types import FrontendMapPayload, MapAction, ResultCard


@dataclass(frozen=True)
class MapActionBuilder:
    ontology: Ontology
    resolver: Any

    def show_objects(self, args: dict[str, Any],
                     allowed_object_types: Collection[str]) -> str:
        requested = args.get("objects") or []
        if not isinstance(requested, list):
            return _error("objects must be an array")

        allowed = frozenset(allowed_object_types)
        actions: list[MapAction] = []
        cards: list[ResultCard] = []
        for index, item in enumerate(requested):
            if not isinstance(item, dict):
                return _error("each objects item must be an object")
            object_type = str(item.get("object_type") or "")
            if object_type not in allowed:
                return _error(f"object_type outside presentation tool scope: {object_type}")
            filters = item.get("filters") or {}
            if not isinstance(filters, dict):
                return _error(f"filters for {object_type} must be an object")

            raw_object_ids = item.get("object_ids") or []
            if not isinstance(raw_object_ids, list):
                return _error(f"object_ids for {object_type} must be an array")
            simplify_tolerance = item.get("simplify_tolerance")
            if (
                simplify_tolerance is not None
                and (
                    not isinstance(simplify_tolerance, (int, float))
                    or isinstance(simplify_tolerance, bool)
                )
            ):
                return _error(
                    f"simplify_tolerance for {object_type} must be a number"
                )

            label = str(
                item.get("label")
                or self.default_object_label(object_type, filters)
                or object_type
            )
            fit = bool(item.get("fit")) if "fit" in item else index == 0
            object_ids = [
                str(value)
                for value in raw_object_ids
                if value not in (None, "")
            ]
            highlight = bool(item.get("highlight")) and bool(object_ids)
            show_only_object_ids = (
                bool(item.get("show_only_object_ids")) and bool(object_ids)
            )

            hydrodynamic_plan = build_hydrodynamic_action_plan(
                object_type,
                filters,
                label=label,
                fit=fit,
                refresh=bool(item.get("refresh", True)),
            )
            if hydrodynamic_plan:
                actions.extend(hydrodynamic_plan.actions)
                object_type = hydrodynamic_plan.object_type
                filters = hydrodynamic_plan.filters
            else:
                actions.extend(_object_actions(
                    item,
                    object_type=object_type,
                    filters=filters,
                    label=label,
                    fit=fit,
                    object_ids=object_ids,
                    highlight=highlight,
                    show_only_object_ids=show_only_object_ids,
                    simplify_tolerance=(
                        float(simplify_tolerance)
                        if simplify_tolerance is not None
                        else None
                    ),
                    clear_highlights=not any(
                        action.get("type") == "clear_highlights"
                        for action in actions
                    ),
                ))

            count = len(object_ids) if highlight else self.count_object(
                object_type, filters,
            )
            cards.append({
                "title": label,
                "value": str(count),
                "detail": (
                    f"{self.object_label(object_type)} 受影响对象已高亮"
                    if highlight
                    else f"{self.object_label(object_type)} 对象已加入地图显示"
                ),
            })

        context = str(args.get("context") or default_context(actions))
        note = str(args.get("note") or default_note(actions))
        return _payload(
            context=context,
            actions=dedupe_actions(actions),
            cards=cards,
            note=note,
        )

    def clear_map(self, args: dict[str, Any]) -> str:
        target = str(args.get("target") or "map")
        if target == "inundation":
            return _payload(
                context=str(args.get("context") or "淹没结果 · 已隐藏"),
                actions=[{"type": "clear_hydrodynamic_result"}],
                cards=[],
                note=str(args.get("note") or "已隐藏淹没范围。"),
            )
        if target != "map":
            return _error(f"unsupported clear target: {target}")
        return _payload(
            context=str(args.get("context") or "基础态 · 领域对象地图"),
            actions=[{"type": "reset"}],
            cards=[],
            note=str(args.get("note") or "已重置地图显示。"),
        )

    def set_inundation_alert(self, args: dict[str, Any]) -> str:
        active = args.get("active")
        if not isinstance(active, bool):
            return _error("active must be a boolean")
        return _payload(
            context=(
                "24小时淹没警戒 · 珊瑚河流域"
                if active else "24小时无淹没 · 珊瑚河流域"
            ),
            actions=[{
                "type": "set_watershed_inundation_alert",
                "active": active,
            }],
            cards=[],
            note=(
                "已显示珊瑚河流域预测淹没警戒边界。"
                if active else "已清除珊瑚河流域预测淹没警戒边界。"
            ),
        )

    def focus_object(self, args: dict[str, Any],
                     allowed_object_types: Collection[str]) -> str:
        object_type = str(args.get("object_type") or "")
        object_id = str(args.get("object_id") or "")
        if object_type and object_type not in frozenset(allowed_object_types):
            return _error(f"object_type outside presentation tool scope: {object_type}")

        title = self.object_label(object_type) if object_type else "选中对象"
        action: MapAction = {"type": "focus_object"}
        if object_type:
            action["object_type"] = object_type
        if object_id:
            action["object_id"] = object_id
        return _payload(
            context=str(args.get("context") or "对象定位 · 珊瑚河流域"),
            actions=[action],
            cards=[{
                "title": title,
                "value": object_id or "当前选中对象",
                "detail": "前端将定位、打开并高亮该对象",
            }],
            note=str(args.get("note") or "已聚焦到当前选中对象。"),
        )

    def show_event_marker(self, args: dict[str, Any],
                          allowed_source_types: Collection[str]) -> str:
        event = args.get("event") or {}
        if not isinstance(event, dict):
            return _error("event must be an object")
        if event.get("longitude") is None or event.get("latitude") is None:
            return _error("event marker requires longitude and latitude")

        allowed = frozenset(allowed_source_types)
        actions: list[MapAction] = []
        source_type = str(event.get("source_type") or "")
        if args.get("show_source"):
            if source_type not in allowed:
                return _error(
                    f"event source_type outside presentation tool scope: {source_type}"
                )
            actions.append({
                "type": "load_object",
                "object_type": source_type,
                "label": self.object_label(source_type),
                "filters": {},
                "fit": False,
            })
        actions.append({
            "type": "show_event_marker",
            "event": event,
            "fit": bool(args.get("fit")) if "fit" in args else True,
        })
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        return _payload(
            context=str(args.get("context") or "事件告警 · 珊瑚河流域"),
            actions=actions,
            cards=[{
                "title": str(event.get("title") or event.get("event_type") or "领域事件"),
                "value": str(payload.get("value") or event.get("severity") or ""),
                "detail": str(payload.get("station_name") or event.get("source_id") or ""),
            }],
            note=str(args.get("note") or "已在地图上显示事件 marker。"),
        )

    def object_label(self, object_type: str) -> str:
        object_def = self.ontology.objects.get(object_type)
        if object_def and object_def.display_name:
            return object_def.display_name
        return str(
            (MAPPABLE_OBJECTS.get(object_type) or {}).get("label")
            or object_type
        )

    def default_object_label(self, object_type: str,
                             filters: dict[str, Any]) -> str:
        hydrodynamic_label = default_hydrodynamic_label(object_type, filters)
        return hydrodynamic_label or self.object_label(object_type)

    def count_object(self, object_type: str,
                     filters: dict[str, Any]) -> int:
        hydrodynamic_count = count_hydrodynamic(object_type, filters)
        if hydrodynamic_count is not None:
            return hydrodynamic_count
        return int(self.resolver.count(object_type, filters))


def _object_actions(
    item: dict[str, Any],
    *,
    object_type: str,
    filters: dict[str, Any],
    label: str,
    fit: bool,
    object_ids: list[str],
    highlight: bool,
    show_only_object_ids: bool,
    simplify_tolerance: float | None,
    clear_highlights: bool,
) -> list[MapAction]:
    action: MapAction = {
        "type": "load_object",
        "object_type": object_type,
        "label": label,
        "filters": filters,
        "object_ids": object_ids if show_only_object_ids else [],
        "replace_object_type": show_only_object_ids,
        "fit": fit and not highlight,
    }
    if item.get("refresh") is not None:
        action["refresh"] = bool(item.get("refresh"))
    if simplify_tolerance is not None:
        action["simplify_tolerance"] = simplify_tolerance

    actions = [action]
    if highlight:
        if clear_highlights:
            actions.append({"type": "clear_highlights"})
        actions.append({
            "type": "highlight_objects",
            "object_type": object_type,
            "object_ids": object_ids,
            "filters": filters,
            "label": label,
            "fit": fit,
        })
    return actions


def tool_result_to_map_event(result: str) -> dict[str, Any] | None:
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


def dedupe_actions(actions: list[MapAction]) -> list[MapAction]:
    seen = set()
    result = []
    for action in actions:
        key = json.dumps(action, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def default_context(actions: list[MapAction]) -> str:
    types = {action.get("object_type") for action in actions}
    action_types = {action.get("type") for action in actions}
    if "apply_hydrodynamic_result" in action_types:
        return "淹没结果 · 珊瑚河流域"
    if "show_hydrodynamic_mesh" in action_types:
        return "水动力网格 · 珊瑚河流域"
    if "InundationForecastCell" in types:
        return "实时预测 · 珊瑚河流域"
    if types & {"Reservoir", "Sluice", "HydraulicStructure"}:
        return "水利工程设施 · 珊瑚河流域"
    if types & {"Road", "Bridge"}:
        return "交通基础设施 · 珊瑚河流域"
    return "对象分析 · 珊瑚河流域"


def default_note(actions: list[MapAction]) -> str:
    labels = [
        str(action.get("label") or action.get("object_type") or "水动力网格")
        for action in actions
    ]
    return f"已在地图上显示：{'、'.join(labels)}。" if labels else "地图动作已执行。"


def _payload(*, context: str, actions: list[MapAction],
             cards: list[ResultCard], note: str) -> str:
    payload: FrontendMapPayload = {
        "kind": "frontend_map_actions",
        "context": context,
        "map_actions": actions,
        "result_cards": cards,
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)
