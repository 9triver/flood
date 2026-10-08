"""Compare destination capacity and exposure; separately recheck a planned route."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .forecast_context import resolve_routing_context, unavailable_forecast
from .forecast_geometry import row_point, distance_m
from .forecast_query import query_forecast_cells
from .impact_analysis import analyze_inundation_impacts
from .object_sets import read_object_set, save_object_set, set_summary
from .route_safety import build_flood_avoidance_areas, path_intersects_areas


def compare_evacuation_sites(resolver, evacuation_unit_id: str, object_set_id: str = "",
                             required_capacity: int | None = None, view: str = "current",
                             time_h: float | None = None, forecast_id: str = "latest",
                             object_ids: list[str] | None = None) -> dict:
    try:
        if object_set_id:
            if object_ids is not None:
                raise ValueError("object_set_id 与 object_ids 不能同时提供")
            selected = read_object_set(object_set_id, "EvacuationSite")
            candidate_ids = selected["object_ids"]
        else:
            if not isinstance(object_ids, list) or any(not isinstance(ident, str) or not ident for ident in object_ids):
                raise ValueError("请提供候选 object_set_id 或 object_ids 列表；空列表表示没有候选点")
            candidate_ids = list(dict.fromkeys(object_ids))
        unit = resolver.query_by_id("EvacuationUnit", evacuation_unit_id)
        if not unit or not row_point(unit):
            raise ValueError("转移单元不存在或缺少位置")
        required = unit.get("population") if required_capacity is None else required_capacity
        if isinstance(required, bool) or not isinstance(required, int) or required <= 0:
            raise ValueError("需要正整数 required_capacity 或可靠的转移单元人口")
        context = resolve_routing_context(forecast_id, time_h=time_h, view=view)
        if not context["available"]:
            return unavailable_forecast(context)
        impacted = set()
        if context["constraint_source"] == "forecast":
            result = analyze_inundation_impacts(resolver, forecast_id=forecast_id, target_type="EvacuationSite",
                                                object_ids=candidate_ids, time_h=context["time_h"])
            if result.get("error"):
                return result
            impacted = set(result.get("affected_object_ids", {}).get("EvacuationSite", []))
        candidates = []
        for ident in candidate_ids:
            site = resolver.query_by_id("EvacuationSite", ident)
            point = row_point(site) if site else None
            capacity = site.get("capacity_person") if site else None
            reasons = []
            if not isinstance(capacity, (int, float)) or capacity <= 0:
                reasons.append("容量未知")
            elif capacity < required:
                reasons.append("容量不足")
            if not point:
                reasons.append("位置未知")
            if ident in impacted:
                reasons.append("对应预测范围内受淹")
            candidates.append({"object_id": ident, "name": (site or {}).get("name", ident),
                               "capacity_person": capacity, "eligible": not reasons, "reasons": reasons,
                               "distance_m": round(distance_m(row_point(unit), point), 1) if point else None})
        candidates.sort(key=lambda row: (not row["eligible"], row["distance_m"] if row["distance_m"] is not None else float("inf"), row["object_id"]))
        eligible = [row["object_id"] for row in candidates if row["eligible"]]
        result = {"status": "completed", "origin_unit_id": evacuation_unit_id, "required_capacity": required,
                "candidates": candidates, "eligible_object_ids": eligible, "eligible_set": None,
                "recommended_site_id": eligible[0] if eligible else None, "forecast_context": context,
                "route_checked": False, "basis": "按容量及地点预测受淹情况筛选，再按直线距离排序；尚未验证路线可达性，也未预留床位。"}
        try:
            result_set = save_object_set("EvacuationSite", eligible, parent_set_id=object_set_id or None,
                                        basis={"required_capacity": required, "forecast_context": context})
            result["eligible_set"] = set_summary(result_set)
        except (OSError, ValueError):
            result["set_storage_warning"] = "候选集合未能保存，可使用 eligible_object_ids 继续分析或展示。"
        return result
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}


def review_route(resolver, evacuation_route_id: str, view: str = "current",
                 time_h: float | None = None, forecast_id: str = "latest") -> dict:
    route = resolver.query_by_id("EvacuationRoute", evacuation_route_id)
    if not route:
        return {"error": "路线不存在，请确认当前工作空间和路线 ID"}
    context = resolve_routing_context(forecast_id, time_h=time_h, view=view)
    if not context["available"]:
        return {**unavailable_forecast(context), "passable": None, "evacuation_route_id": evacuation_route_id}
    try:
        geometry = route.get("geometry") or {}
        geometry = json.loads(geometry) if isinstance(geometry, str) else geometry
        if geometry.get("type") != "LineString" or len(geometry.get("coordinates", [])) < 2:
            raise ValueError("缺少可复核的路线线形")
        blocked = False
        threshold = route.get("blocked_depth_m")
        if threshold is None:
            threshold = 0.15 if route.get("profile") == "foot" else 0.3
        if context["constraint_source"] == "forecast":
            filters = {"forecast_id": forecast_id}
            if context["time_h"] is not None:
                filters["time_h"] = context["time_h"]
            areas = build_flood_avoidance_areas(query_forecast_cells(filters), threshold)
            blocked = path_intersects_areas(geometry["coordinates"], areas["feature_collection"])
        return {"status": "completed", "evacuation_route_id": evacuation_route_id,
                "passable": not blocked, "forecast_context": context,
                "previous_flood_validation": route.get("flood_validation"),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "basis": "按当前演示洪水状态复核既有线形，不重新调用路由引擎；不包含现场封路和道路实时状况。"}
    except (ValueError, TypeError, KeyError) as exc:
        return {"error": str(exc), "passable": None}
