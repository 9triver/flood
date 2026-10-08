from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from typing import Any, Collection

from oag.ontology.schema import Ontology

from domains.flood.runtime.object_sets import read_object_set, save_object_set
from domains.flood.runtime.common import MAPPABLE_OBJECTS, id_field, apply_filters
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

    def _filters(self, object_type: str, filters: Any) -> dict:
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        fields = set(self.ontology.objects[object_type].properties)
        for key in filters:
            field, _, op = key.partition("__")
            if field not in fields or op not in {"", "eq", "ne", "in", "like", "gt", "gte", "lt", "lte"}:
                raise ValueError(f"unsupported filter for {object_type}: {key}")
        return dict(filters)

    def _ids(self, values: Any) -> list[str]:
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("object_ids must be an array of nonempty strings")
        return list(dict.fromkeys(values))

    def show_objects(self, args: dict[str, Any],
                     allowed_object_types: Collection[str]) -> str:
        requested = args.get("objects")
        if not isinstance(requested, list) or not requested:
            return _error("objects must be a nonempty array")
        actions: list[MapAction] = []
        cards: list[ResultCard] = []
        selections = []
        try:
            # Resolve and validate the entire batch before emitting any actions.
            for index, item in enumerate(requested):
                if not isinstance(item, dict):
                    raise ValueError("each objects item must be an object")
                item = dict(item)
                if item.get("object_set_id"):
                    if "object_ids" in item:
                        raise ValueError("object_set_id cannot be combined with object_ids")
                    selected = read_object_set(item["object_set_id"], item.get("object_type"))
                    item["object_type"] = selected["object_type"]
                    item["object_ids"] = selected["object_ids"]
                object_type = item.get("object_type")
                if object_type not in allowed_object_types:
                    raise ValueError(f"object_type outside presentation tool scope: {object_type}")
                if "show_only_object_ids" in item:
                    raise ValueError("show_only_object_ids was removed; object_ids always limits the displayed set")
                replace_ids = None
                if item.get("replace_object_set_id"):
                    if not item.get("object_set_id") or item.get("mode", "add") != "add":
                        raise ValueError("replace_object_set_id requires object_set_id and cannot be combined with mode=replace")
                    replace_ids = read_object_set(item["replace_object_set_id"], object_type)["object_ids"]
                mode = item.get("mode", "add")
                if mode not in {"add", "replace"}:
                    raise ValueError("mode must be add or replace")
                for key in ("fit", "refresh", "highlight"):
                    if key in item and not isinstance(item[key], bool):
                        raise ValueError(f"{key} must be a boolean")
                tolerance = item.get("simplify_tolerance")
                if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0):
                    raise ValueError("simplify_tolerance must be a finite nonnegative number")
                filters = item.get("filters", {})
                if not isinstance(filters, dict):
                    raise ValueError("filters must be an object")
                label = str(item.get("label") or self.default_object_label(object_type, filters))
                fit = item.get("fit", index == 0)
                if object_type in {"HydrodynamicGridCell", "InundationForecastCell"}:
                    if "object_ids" in item or item.get("highlight") or mode != "add":
                        raise ValueError("hydrodynamic display does not support object_ids, highlight or replace; use forecast_id/time_h/view filters")
                    plan = build_hydrodynamic_action_plan(object_type, filters, label=label, fit=fit, refresh=item.get("refresh", True))
                    actions.extend(plan.actions)
                    count = self.count_object(plan.object_type, plan.filters)
                    cards.append({"title": label, "value": str(count), "detail": "预测包络湿网格数" if plan.filters.get("forecast_id") else "模型网格数"})
                    continue
                filters = self._filters(object_type, filters)
                ids = self._ids(item["object_ids"]) if "object_ids" in item else None
                if ids is not None:
                    # An empty set never means all objects, including in replace mode.
                    if not ids:
                        if replace_ids is not None:
                            actions.append({"type": "hide_objects", "object_type": object_type, "object_ids": replace_ids})
                        detail = "对象集合为空，已请求移除被替换集合" if replace_ids is not None else "对象集合为空，未请求地图变更"
                        cards.append({"title": label, "value": "0", "detail": detail})
                        continue
                    field = id_field(object_type)
                    rows = apply_filters(self.resolver.query(object_type, {f"{field}__in": ids}), filters)
                    matched = {str(row.get(field)) for row in rows}
                    if item.get("object_set_id") and filters:
                        source_set_id = item["object_set_id"]
                        ids = [value for value in ids if value in matched]
                        try:
                            subset = save_object_set(object_type, ids, parent_set_id=source_set_id,
                                                     basis={"filters": filters})
                            item["object_set_id"] = subset["object_set_id"]
                        except (OSError, ValueError):
                            # Visible selection IDs still describe the exact
                            # displayed subset; do not label it as the parent.
                            item["object_set_id"] = None
                    missing = [value for value in ids if value not in matched]
                    if missing:
                        raise ValueError(f"objects not found or excluded by filters: {object_type} {missing}")
                    count = len(ids)
                else:
                    count = self.resolver.count(object_type, filters)
                if not count:
                    if replace_ids is not None:
                        actions.append({"type": "hide_objects", "object_type": object_type, "object_ids": replace_ids})
                    detail = "没有匹配对象，已请求移除被替换集合" if replace_ids is not None else "没有匹配对象，未请求地图变更"
                    cards.append({"title": label, "value": "0", "detail": detail})
                    continue
                selection_id = f"selection_{uuid.uuid4().hex}"
                action = {"type": "load_object", "object_type": object_type,
                          "filters": filters, "label": label, "fit": fit,
                          "mode": mode, "highlight": item.get("highlight", False),
                          "selection_id": selection_id, "refresh": item.get("refresh", False), "object_set_id": item.get("object_set_id")}
                if replace_ids is not None:
                    action["replace_object_ids"] = replace_ids
                if ids is not None:
                    action["object_ids"] = ids
                if tolerance is not None:
                    action["simplify_tolerance"] = tolerance
                actions.append(action)
                selections.append({"selection_id": selection_id, "object_type": object_type, "count": count, "label": label, "object_set_id": item.get("object_set_id")})
                cards.append({"title": label, "value": str(count), "detail": "匹配对象数；已请求显示"})
        except (ValueError, TypeError) as exc:
            return _error(str(exc))
        # replace applies once per type, so multiple groups in one batch survive.
        replaced = set()
        for action in actions:
            if action.get("mode") == "replace":
                object_type = action["object_type"]
                if object_type in replaced:
                    action["mode"] = "add"
                replaced.add(object_type)
        # Move each replacement ahead of other additions of that type.
        actions.sort(key=lambda action: 0 if action.get("mode") == "replace" else 1)
        return _payload(context=str(args.get("context") or default_context(actions)),
                        actions=actions, cards=cards, note="已请求更新指定候选集合。" if any(item.get("replace_object_set_id") for item in requested) else default_note(actions), selections=selections)

    def hide_objects(self, args: dict[str, Any], allowed_object_types: Collection[str]) -> str:
        selection_id = args.get("selection_id")
        if selection_id:
            if any(key in args for key in ("object_type", "object_ids", "filters")):
                return _error("use selection_id or object_type/object_ids/filters, not both")
            action = {"type": "hide_objects", "selection_id": str(selection_id)}
        else:
            object_type = args.get("object_type")
            if object_type not in allowed_object_types:
                return _error("object_type is required and must be within presentation tool scope")
            if object_type == "HydrodynamicGridCell" and not args.get("filters") and "object_ids" not in args:
                return _payload(context="隐藏模型网格", actions=[{"type": "hide_hydrodynamic_mesh"}], cards=[], note="已请求隐藏模型网格，保留预测淹没结果。")
            if object_type in {"HydrodynamicGridCell", "InundationForecastCell"}:
                return _error("use ui_hide_forecast for forecast results; individual grid-cell hiding is unsupported")
            if "object_ids" not in args and not args.get("filters"):
                return _payload(context="隐藏指定类型", actions=[{"type": "hide_objects", "object_type": object_type}], cards=[], note="已请求隐藏该类型的当前可见对象。")
            try:
                filters = self._filters(object_type, args.get("filters", {}))
                rows = self.resolver.query(object_type, filters)
                ids = [str(row[id_field(object_type)]) for row in rows]
                if "object_ids" in args:
                    requested = self._ids(args["object_ids"])
                    ids = [value for value in requested if value in set(ids)]
            except (ValueError, TypeError) as exc:
                return _error(str(exc))
            action = {"type": "hide_objects", "object_type": object_type, "object_ids": ids}
        return _payload(context="隐藏指定对象", actions=[action], cards=[], note="已请求隐藏指定对象；其他图层和视野保持不变。")

    def reset_map(self, args: dict[str, Any]) -> str:
        return _payload(context="基础态 · 领域对象地图", actions=[{"type": "reset"}],
                        cards=[], note="已请求重置整个地图。")

    def hide_forecast(self, args: dict[str, Any]) -> str:
        return _payload(context="隐藏预测淹没结果", actions=[{"type": "clear_hydrodynamic_result"}],
                        cards=[], note="已请求隐藏预测淹没结果。")

    def set_inundation_alert(self, args: dict[str, Any]) -> str:
        active = args.get("active")
        if not isinstance(active, bool):
            return _error("active must be a boolean")
        return _payload(
            context=(
                "24小时淹没警戒 · 珊瑚河流域"
                if active else "隐藏流域淹没警戒"
            ),
            actions=[{
                "type": "set_watershed_inundation_alert",
                "active": active,
            }],
            cards=[],
            note=(
                "已请求显示珊瑚河流域预测淹没警戒边界。"
                if active else "已请求清除珊瑚河流域预测淹没警戒边界。"
            ),
        )

    def focus_object(self, args: dict[str, Any],
                     allowed_object_types: Collection[str]) -> str:
        object_type = str(args.get("object_type") or "")
        object_id = str(args.get("object_id") or "")
        if object_type and object_type not in frozenset(allowed_object_types):
            return _error(f"object_type outside presentation tool scope: {object_type}")

        if not object_type or not object_id:
            return _error("object_type and object_id are required; resolve the selected object from frontend context")
        if object_type in {"HydrodynamicGridCell", "InundationForecastCell", "Watershed", "County", "Town"}:
            return _error("this object type does not support individual focus; use ui_show_objects with fit=true")
        if not self.resolver.query_by_id(object_type, object_id):
            return _error(f"object not found: {object_type} {object_id}")
        return _payload(context="对象定位", actions=[{"type": "focus_object", "object_type": object_type, "object_id": object_id}],
                        cards=[], note="已请求定位指定对象。")

    def show_event_marker(self, args: dict[str, Any],
                          allowed_source_types: Collection[str]) -> str:
        event = args.get("event") or {}
        if not isinstance(event, dict):
            return _error("event must be an object")
        if event.get("longitude") is None or event.get("latitude") is None:
            return _error("event marker requires longitude and latitude")

        try:
            lon, lat = float(event["longitude"]), float(event["latitude"])
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError("invalid event coordinates")
        except (TypeError, ValueError):
            return _error("event coordinates must be valid WGS84 longitude/latitude")
        allowed = frozenset(allowed_source_types)
        actions: list[MapAction] = []
        source_type = str(event.get("source_type") or "")
        if args.get("show_source"):
            if source_type not in allowed:
                return _error(
                    f"event source_type outside presentation tool scope: {source_type}"
                )
            if not event.get("source_id") or not self.resolver.query_by_id(source_type, str(event["source_id"])):
                return _error("event source object not found")
            actions.append({
                "type": "load_object",
                "object_type": source_type,
                "label": self.object_label(source_type),
                "filters": {},
                "object_ids": [str(event.get("source_id"))],
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
            note=str(args.get("note") or "已请求显示事件 marker。"),
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
        "operation_id": payload.get("operation_id"),
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
    if types & {"Road", "RoadRoute", "Bridge"}:
        return "交通基础设施 · 珊瑚河流域"
    return "对象分析 · 珊瑚河流域"


def default_note(actions: list[MapAction]) -> str:
    labels = [
        str(action.get("label") or action.get("object_type") or "水动力网格")
        for action in actions
    ]
    return f"已请求显示：{'、'.join(labels)}；以浏览器执行回执为准。" if labels else "没有匹配对象，未请求地图变更。"


def _payload(*, context: str, actions: list[MapAction],
             cards: list[ResultCard], note: str, selections: list | None = None) -> str:
    payload: FrontendMapPayload = {
        "kind": "frontend_map_actions",
        "status": "pending" if actions else "no_matches",
        "operation_id": uuid.uuid4().hex,
        "selections": selections or [],
        "context": context,
        "map_actions": actions,
        "result_cards": cards,
        "note": note,
    }
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)
