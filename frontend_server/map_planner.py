from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


OBJECT_LABELS = {
    "River": "珊瑚河",
    "Watershed": "珊瑚河流域",
    "Waterway": "河道水系",
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
    "Risk": "危险区",
    "HydroStation": "水文测站",
    "HistoricalFloodMark": "历史洪痕",
    "Cell": "淹没范围",
    "ForecastCell": "预测淹没",
}

ID_FIELDS = {
    "River": "river_id",
    "Watershed": "watershed_id",
    "Waterway": "waterway_id",
    "County": "county_id",
    "Road": "road_id",
    "Reservoir": "reservoir_id",
    "Sluice": "sluice_id",
    "Bridge": "bridge_id",
    "Facility": "facility_id",
    "HydraulicStructure": "structure_id",
    "Place": "place_id",
    "Transfer": "transfer_id",
    "Route": "route_id",
    "Risk": "risk_id",
    "HydroStation": "station_id",
    "HydroObservation": "observation_id",
    "HistoricalFloodMark": "mark_id",
    "Cell": "cell_id",
    "ForecastCell": "forecast_cell_id",
}

OBJECT_KEYWORDS = {
    "River": ["珊瑚河", "河道中心线", "主河道"],
    "Reservoir": ["水库"],
    "Sluice": ["水闸", "闸门", "分洪闸"],
    "HydraulicStructure": ["堤防", "泵站", "溢流", "水利工程", "水利设施"],
    "Bridge": ["桥", "桥梁"],
    "Road": ["道路", "公路", "路段"],
    "Facility": ["学校", "医院", "政府", "设施"],
    "Place": ["安置点", "安置地点"],
    "Route": ["路线", "转移路线"],
    "Transfer": ["转移对象", "转移单元"],
    "Waterway": ["河道", "水系", "河网"],
    "Watershed": ["流域"],
    "Risk": ["危险区", "风险点", "危险村", "危险屯"],
    "HydroStation": ["水文站", "测站", "雨量站", "水位站", "气象站", "水文测站"],
    "HistoricalFloodMark": ["洪痕", "历史洪水", "洪水记录", "洪水调查"],
    "ForecastCell": ["预测淹没", "未来淹没", "实时预测", "洪水预测", "运行预测", "预报淹没"],
    "County": ["行政边界", "县界", "县"],
}


@dataclass
class DisplayIntent:
    """A UI intent, not a domain fact."""

    reset: bool = False
    return_period_year: int = 0
    show_flood: bool = False
    show_forecast: bool = False
    run_cycle: bool = False
    show_traffic: bool = False
    show_waterway: bool = False
    show_river: bool = False
    show_risk: bool = False
    water_objects: list[str] = field(default_factory=list)
    facility_type: str = ""
    facility_label: str = ""
    show_evacuation: bool = False
    focus_selected: bool = False
    focus_object_type: str = ""
    focus_object_id: str = ""


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
        intent.show_forecast = any(word in text for word in ["预测", "预报", "未来淹没", "实时", "运行模型", "模型运行", "自动观测"])
        intent.run_cycle = any(word in text for word in ["闭环", "自动告警", "持续预测", "自主观测", "调度", "应急循环", "自动转移"])
        intent.show_flood = any(word in text for word in ["洪水", "淹没", "受淹", "水深", "风险", "影响", "最不利"])
        intent.show_waterway = any(word in text for word in ["河道", "水系", "河流线", "河网"]) or (
            any(word in text for word in ["河流", "珊瑚河"]) and any(word in text for word in ["显示", "打开", "绘制", "画"])
        )
        intent.show_river = any(word in text for word in ["珊瑚河", "河道中心线", "主河道"]) and any(word in text for word in ["显示", "打开", "绘制", "画"])
        intent.show_risk = any(word in text for word in ["危险区", "风险点", "危险村", "危险屯"])
        intent.show_traffic = any(word in text for word in ["道路", "公路", "交通", "封控", "中断", "桥", "桥梁"])
        intent.show_evacuation = any(word in text for word in ["转移", "安置", "避洪", "路线", "撤离"])
        focus_requested = any(word in text for word in ["聚焦", "定位", "缩放到", "高亮", "查看"])
        intent.focus_selected = bool(selected.get("object_type")) and (
            any(word in text for word in ["这个", "这条", "该", "它"]) or focus_requested
        )
        if intent.focus_selected:
            intent.focus_object_type = selected.get("object_type", "")
            intent.focus_object_id = selected.get("id", "")
        elif focus_requested:
            target = self._resolve_focus_target(message)
            if target:
                intent.focus_object_type = target["object_type"]
                intent.focus_object_id = target["object_id"]
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
            actions.append({
                "type": "focus_object",
                "object_type": intent.focus_object_type,
                "object_id": intent.focus_object_id,
            })
            notes.append(self._selected_note(selected))

        if intent.run_cycle:
            context = "闭环预警 · 珊瑚河流域"
            result = self.registry.call("run_emergency_cycle", force_forecast=False)
            forecast = result.get("forecast") or {}
            warning = result.get("warning") or {}
            actions.extend([
                {"type": "load_object", "object_type": "ForecastCell", "label": "预测淹没单元", "filters": {"forecast_id": "latest"}, "simplify_tolerance": 5, "fit": True},
                {"type": "load_object", "object_type": "Risk", "label": "危险区", "filters": {"risk_type": "danger_area"}, "fit": False},
                {"type": "load_object", "object_type": "Transfer", "label": "转移对象", "filters": {}, "fit": False},
                {"type": "load_object", "object_type": "Place", "label": "安置地点", "filters": {}, "fit": False},
                {"type": "load_object", "object_type": "Route", "label": "转移路线", "filters": {}, "fit": False},
            ])
            cards.extend([
                {
                    "title": warning.get("title", "洪水预警"),
                    "value": str(warning.get("level", "")).upper(),
                    "detail": warning.get("basis", ""),
                },
                {
                    "title": "预测淹没",
                    "value": f"{forecast.get('inundated_area_km2', 0):.2f} km²",
                    "detail": f"最大水深 {forecast.get('max_depth_m', 0):.2f} m，预测单元 {forecast.get('forecast_cell_count', 0)} 个",
                },
                {
                    "title": "调度建议",
                    "value": str(len(result.get("recommendations") or [])),
                    "detail": "建议仍需人工审批后执行，当前为闭环原型输出",
                },
            ])
            notes.append("已完成一次观测-预测-告警-调度闭环原型运行，并把预测淹没、危险区和转移调度对象加载到地图。")

        elif intent.show_forecast:
            context = "实时预测 · 珊瑚河流域"
            summary = self.registry.call("run_flood_forecast", forecast_id="latest")
            forecast = summary.get("forecast") or {}
            actions.append({
                "type": "load_object",
                "object_type": "ForecastCell",
                "label": "预测淹没单元",
                "filters": {"forecast_id": "latest"},
                "simplify_tolerance": 5,
                "fit": True,
            })
            cards.append({
                "title": "洪水预测运行",
                "value": f"{forecast.get('inundated_area_km2', 0):.2f} km²",
                "detail": f"最大水深 {forecast.get('max_depth_m', 0):.2f} m，预测单元 {forecast.get('forecast_cell_count', 0)} 个，预见期 {forecast.get('lead_time_h', 0):.1f} h",
            })
            notes.append("已运行简化水动力预测，并直接展示模型输出的预测淹没单元。")

        elif intent.show_flood:
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

        if intent.show_waterway:
            if intent.show_river:
                actions.append({
                    "type": "load_object",
                    "object_type": "River",
                    "label": "珊瑚河",
                    "filters": {},
                    "fit": True,
                })
                cards.append({
                    "title": "珊瑚河",
                    "value": str(self.resolver.count("River")),
                    "detail": "本地河道中心线对象，作为珊瑚河河流主体的地图几何",
                })
            actions.append({
                "type": "load_object",
                "object_type": "Waterway",
                "label": "河道水系",
                "filters": {},
                "fit": False,
            })
            cards.append({
                "title": "河道水系",
                "value": str(self.resolver.count("Waterway")),
                "detail": "OSM 未命名为珊瑚河的 waterway 候选线，按与流域相交关系纳入对象库",
            })
            notes.append("已打开河道水系候选对象。")

        elif intent.show_river:
            actions.append({
                "type": "load_object",
                "object_type": "River",
                "label": "珊瑚河",
                "filters": {},
                "fit": True,
            })
            cards.append({
                "title": "珊瑚河",
                "value": str(self.resolver.count("River")),
                "detail": "本地河道中心线对象，已从 CGCS2000 投影转换为 WGS84 用于前端绘制",
            })
            notes.append("已打开珊瑚河河道中心线。")

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

        if intent.show_risk:
            context = "危险区 · 珊瑚河流域"
            actions.append({
                "type": "load_object",
                "object_type": "Risk",
                "label": "危险区",
                "filters": {"risk_type": "danger_area"},
                "fit": True,
            })
            cards.append({
                "title": "危险区",
                "value": str(self.resolver.count("Risk", {"risk_type": "danger_area"})),
                "detail": "由危险区台账经纬度生成点对象，可与淹没范围和转移对象叠加",
            })
            notes.append("已打开危险区点对象。")

        if intent.focus_object_type and intent.focus_object_id and not intent.focus_selected:
            context = "对象定位 · 珊瑚河流域"
            actions.append({
                "type": "focus_object",
                "object_type": intent.focus_object_type,
                "object_id": intent.focus_object_id,
            })
            cards.append({
                "title": OBJECT_LABELS.get(intent.focus_object_type, intent.focus_object_type),
                "value": intent.focus_object_id,
                "detail": "已定位并高亮该对象",
            })
            notes.append("已定位到指定对象。")

        if not actions:
            actions.extend([
                {"type": "load_object", "object_type": "Watershed", "label": "珊瑚河流域", "filters": {}, "fit": True},
                {"type": "load_object", "object_type": "County", "label": "行政边界", "filters": {}, "fit": False},
            ])
            cards.extend([
                {"title": "流域", "value": str(self.resolver.count("Watershed")), "detail": "珊瑚河流域范围"},
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

    def _resolve_focus_target(self, message: str) -> dict[str, str] | None:
        object_types = self._object_types_for_text(message) or list(ID_FIELDS)
        for object_type in object_types:
            id_field = ID_FIELDS.get(object_type)
            if not id_field:
                continue
            for row in self.resolver.query(object_type):
                object_id = str(row.get(id_field) or "")
                if object_id and object_id in message:
                    return {"object_type": object_type, "object_id": object_id}
        for object_type in object_types:
            id_field = ID_FIELDS.get(object_type)
            if not id_field:
                continue
            for row in self.resolver.query(object_type):
                name = str(row.get("name") or "")
                object_id = str(row.get(id_field) or "")
                if name and name in message and object_id:
                    return {"object_type": object_type, "object_id": object_id}
        return None

    @staticmethod
    def _object_types_for_text(message: str) -> list[str]:
        result = []
        for object_type, keywords in OBJECT_KEYWORDS.items():
            if any(keyword in message for keyword in keywords):
                result.append(object_type)
        return result

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
