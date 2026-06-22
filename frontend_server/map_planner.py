from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


OBJECT_LABELS = {
    "River": "珊瑚河流域",
    "County": "行政边界",
    "Road": "道路",
    "Reservoir": "水库",
    "Sluice": "水闸",
    "Bridge": "桥梁",
    "Facility": "重要设施",
    "HydraulicStructure": "水利工程",
    "Place": "安置地点",
    "Transfer": "转移对象",
    "Route": "转移路线",
    "Cell": "淹没范围",
}


@dataclass
class DisplayIntent:
    """A UI intent, not a domain fact."""

    reset: bool = False
    return_period_year: int = 0
    show_flood: bool = False
    show_traffic: bool = False
    water_objects: list[str] = field(default_factory=list)
    facility_type: str = ""
    facility_label: str = ""
    show_evacuation: bool = False
    focus_selected: bool = False


@dataclass
class MapPlan:
    """Deterministic frontend actions derived from a display intent."""

    context: str
    map_actions: list[dict[str, Any]] = field(default_factory=list)
    result_cards: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "map_actions": self.map_actions,
            "result_cards": self.result_cards,
            "note": self.note,
        }


class MapActionPlanner:
    """Turns display intent into stable GIS actions.

    This planner is deliberately deterministic. The LLM may help identify an
    analysis target later, but map actions such as fit, reset, highlight, and
    card layout should remain product rules.
    """

    def __init__(self, resolver, registry, scenarios: list[dict[str, Any]]):
        self.resolver = resolver
        self.registry = registry
        self.scenarios = scenarios

    def plan(self, message: str, selected: dict[str, Any] | None = None) -> dict[str, Any]:
        selected = selected or {}
        intent = self.intent_from_message(message, selected)
        return self.plan_from_intent(intent, selected).to_dict()

    def intent_from_message(self, message: str, selected: dict[str, Any]) -> DisplayIntent:
        text = message.lower()
        intent = DisplayIntent()

        intent.reset = any(word in text for word in ["清空", "重置", "恢复"])
        if intent.reset:
            return intent

        intent.return_period_year = self._extract_period(text)
        intent.show_flood = any(word in text for word in ["洪水", "淹没", "受淹", "水深", "风险", "影响", "最不利"])
        intent.show_traffic = any(word in text for word in ["道路", "公路", "交通", "封控", "中断", "桥", "桥梁"])
        intent.show_evacuation = any(word in text for word in ["转移", "安置", "避洪", "路线", "撤离"])
        intent.focus_selected = bool(selected.get("object_type")) and any(word in text for word in ["这个", "这条", "该", "它"])
        intent.water_objects = self._water_objects_for_text(text)

        if not intent.water_objects and any(word in text for word in ["学校", "医院", "政府", "设施"]):
            if "医院" in text:
                intent.facility_type = "hospital"
                intent.facility_label = "医院"
            elif "政府" in text:
                intent.facility_type = "government"
                intent.facility_label = "政府机构"
            else:
                intent.facility_type = "school"
                intent.facility_label = "学校"

        return intent

    def plan_from_intent(self, intent: DisplayIntent, selected: dict[str, Any] | None = None) -> MapPlan:
        selected = selected or {}
        if intent.reset:
            return MapPlan(
                context="基础态 · 领域对象地图",
                map_actions=[{"type": "reset"}],
                note="已恢复到基础对象地图。",
            )

        actions: list[dict[str, Any]] = []
        cards: list[dict[str, Any]] = []
        notes: list[str] = []
        context = "对象分析 · 珊瑚河流域"

        if intent.focus_selected:
            cards.append(self._selected_card(selected))
            actions.append({"type": "focus_selected"})
            notes.append(self._selected_note(selected))

        if intent.show_flood:
            scenario = self._scenario_for_period(intent.return_period_year) or self._scenario_for_period(20)
            if scenario:
                context = f"{scenario['return_period_year']} 年一遇 · 洪水影响分析"
                summary = self.registry.call("get_scenario_summary", scenario_id=scenario["scenario_id"])
                impact = summary.get("impact") or {}
                actions.append({
                    "type": "load_object",
                    "object_type": "Cell",
                    "label": f"{scenario['return_period_year']} 年一遇淹没范围",
                    "filters": {"scenario_id": scenario["scenario_id"]},
                    "simplify_tolerance": 5,
                    "fit": True,
                })
                cards.append({
                    "title": f"{scenario['return_period_year']} 年一遇情景",
                    "value": f"{impact.get('inundated_area_km2', 0):.2f} km²",
                    "detail": f"受影响人口 {impact.get('affected_population_10k', 0):.2f} 万人，公路 {impact.get('inundated_road_km', 0):.2f} km，直接损失 {impact.get('direct_loss_10k_cny', 0):.2f} 万元",
                })
                notes.append(f"已按 {scenario['return_period_year']} 年一遇情景展示淹没范围。")

        if intent.show_traffic:
            actions.extend([
                {"type": "load_object", "object_type": "Road", "label": "道路", "filters": {}, "fit": False},
                {"type": "load_object", "object_type": "Bridge", "label": "桥梁", "filters": {}, "fit": False},
            ])
            cards.extend([
                {"title": "道路对象", "value": str(self.resolver.count("Road")), "detail": "来自路网线对象，可与淹没范围叠加研判通行影响"},
                {"title": "桥梁对象", "value": str(self.resolver.count("Bridge")), "detail": "来自桥梁点对象，已保留对象经纬度和 GeoJSON geometry"},
            ])
            notes.append("已打开道路和桥梁对象。")

        if intent.water_objects:
            context = "水利工程设施 · 珊瑚河流域"
            actions.extend(self._water_actions(intent.water_objects))
            cards.extend(self._water_cards(intent.water_objects))
            notes.append("已打开水利工程设施对象。")

        if intent.facility_type:
            actions.append({
                "type": "load_object",
                "object_type": "Facility",
                "label": intent.facility_label,
                "filters": {"facility_type": intent.facility_type},
                "fit": True,
            })
            cards.append({
                "title": intent.facility_label,
                "value": str(self.resolver.count("Facility", {"facility_type": intent.facility_type})),
                "detail": "设施对象自带点 geometry，前端按对象绘制，不依赖领域图层定义",
            })
            notes.append(f"已筛选并高亮 {intent.facility_label} 对象。")

        if intent.show_evacuation:
            actions.extend([
                {"type": "load_object", "object_type": "Place", "label": "安置地点", "filters": {}, "fit": False},
                {"type": "load_object", "object_type": "Route", "label": "转移路线", "filters": {}, "fit": False},
                {"type": "load_object", "object_type": "Transfer", "label": "转移对象", "filters": {}, "fit": False},
            ])
            cards.extend([
                {"title": "安置地点", "value": str(self.resolver.count("Place")), "detail": "包含安置点和就地安置单元"},
                {"title": "转移路线", "value": str(self.resolver.count("Route")), "detail": "路线对象可与道路和淹没范围叠加"},
            ])
            notes.append("已打开转移对象、安置地点和转移路线。")

        if not actions:
            actions.extend([
                {"type": "load_object", "object_type": "River", "label": "珊瑚河流域", "filters": {}, "fit": True},
                {"type": "load_object", "object_type": "County", "label": "行政边界", "filters": {}, "fit": False},
            ])
            cards.extend([
                {"title": "流域", "value": str(self.resolver.count("River")), "detail": "珊瑚河流域范围"},
                {"title": "行政边界", "value": str(self.resolver.count("County")), "detail": "县级行政区边界"},
            ])
            notes.append("已打开珊瑚河流域和行政边界。")

        return MapPlan(
            context=context,
            map_actions=self._dedupe_actions(actions),
            result_cards=cards,
            note="\n".join(notes),
        )

    def _extract_period(self, text: str) -> int:
        if "最不利" in text or "最大" in text:
            return max((row.get("return_period_year") or 0 for row in self.scenarios), default=100)
        match = re.search(r"(5|10|20|50|100)\s*(?:年一遇|年|a)", text)
        return int(match.group(1)) if match else 0

    def _scenario_for_period(self, period: int) -> dict[str, Any] | None:
        if not period:
            return None
        return next((row for row in self.scenarios if row.get("return_period_year") == period), None)

    @staticmethod
    def _water_objects_for_text(text: str) -> list[str]:
        objects: list[str] = []
        broad = any(word in text for word in ["水利设施", "水利工程", "水工设施", "工程设施"])
        if broad or "水库" in text:
            objects.append("Reservoir")
        if broad or any(word in text for word in ["水闸", "闸门", "分洪闸"]):
            objects.append("Sluice")
        if broad or any(word in text for word in ["堤防", "泵站", "溢流", "泄洪", "水利建筑物"]):
            objects.append("HydraulicStructure")
        return objects

    @staticmethod
    def _water_actions(object_types: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "load_object",
                "object_type": object_type,
                "label": OBJECT_LABELS[object_type],
                "filters": {},
                "fit": index == 0,
            }
            for index, object_type in enumerate(object_types)
        ]

    def _water_cards(self, object_types: list[str]) -> list[dict[str, str]]:
        details = {
            "Reservoir": "水库对象承接下游洪水来源、调度情景和工程台账信息",
            "Sluice": "水闸对象表达分洪、泄洪、灌区闸门等工程",
            "HydraulicStructure": "堤防、泵站、溢流建筑物等统一建模为水利工程设施",
        }
        return [
            {
                "title": OBJECT_LABELS[object_type],
                "value": str(self.resolver.count(object_type)),
                "detail": details[object_type],
            }
            for object_type in object_types
        ]

    def _selected_card(self, selected: dict[str, Any]) -> dict[str, str]:
        title = selected.get("name") or selected.get("id") or "选中对象"
        object_type = selected.get("object_type") or ""
        return {
            "title": f"{OBJECT_LABELS.get(object_type, object_type)}",
            "value": str(title),
            "detail": f"对象ID: {selected.get('id', '')}",
        }

    def _selected_note(self, selected: dict[str, Any]) -> str:
        object_type = selected.get("object_type") or ""
        name = selected.get("name") or selected.get("id") or "该对象"
        return f"已把当前分析对象限定为 {OBJECT_LABELS.get(object_type, object_type)}「{name}」。"

    @staticmethod
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
