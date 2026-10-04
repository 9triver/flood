"""Read-only checks tying forecasts to the active simulation clock and inputs."""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from .boundary_flow import read_latest_forecast_input
from .hydrodynamic_grid import (
    forecast_metadata, forecast_depth_path, forecast_series_path,
    forecast_time_steps, forecast_depth_entry, offset_time_iso,
)
from . import workspace
from .workspace import active_workspace_id


ACTIVE_PHASES = {"active", "running", "paused", "processing", "finished"}


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def initial_flood_context() -> dict | None:
    """Only a genuinely unstarted current demo has the dry initial-state contract."""
    wid = active_workspace_id()
    manifest = workspace.WORKSPACES.active_manifest() or {}
    if wid and wid == workspace.WORKSPACES.current_id and manifest.get("status") == "ready" and manifest.get("simulation_time") is None:
        return {"workspace_id": wid, "simulation_time": None, "playback_phase": "ready",
                "status": "initial_dry", "available": True, "forecast_available": False,
                "forecast_id": "", "time_h": None, "view": "current",
                "constraint_source": "initial_state",
                "basis": "演示尚未开始演进，按无洪水初始状态处理，不读取历史预测。"}
    return None


def resolve_routing_context(forecast_id: str = "latest", time_h: float | None = None,
                            view: str = "current") -> dict:
    initial = initial_flood_context()
    if initial and forecast_id in ("", "latest", "forecast_latest") and time_h in (None, "") and view == "current":
        return initial
    context = resolve_forecast_context(forecast_id, time_h, view)
    return {**context, "constraint_source": "forecast", "forecast_available": context["available"]}


def forecast_input_context() -> dict:
    workspace_id = active_workspace_id()
    manifest = workspace.WORKSPACES.active_manifest() or {}
    result = {"workspace_id": workspace_id, "simulation_time": manifest.get("simulation_time"),
              "playback_phase": manifest.get("status", "ready"), "available": False}
    if not workspace_id or workspace_id != workspace.WORKSPACES.current_id:
        return {**result, "status": "inactive_workspace", "reason": "该工作空间不是当前演示。"}
    if manifest.get("status") not in ACTIVE_PHASES or _time(result["simulation_time"]) is None:
        return {**result, "status": "not_started", "reason": "当前没有有效的演进预测；未开始演进仅可按无洪水初始状态处理，不能据此判断未来淹没。"}
    snapshot = read_latest_forecast_input() or {}
    summary = snapshot.get("summary") or {}
    input_id = summary.get("boundary_flow_id")
    start, end = _time(summary.get("window_start")), _time(summary.get("window_end"))
    current = _time(result["simulation_time"])
    if not input_id or not start or not end or not start <= current <= end:
        return {**result, "status": "no_current_input", "reason": "当前演进时刻没有适用的预测输入，请先演进至预测触发时刻。"}
    return {**result, "available": True, "status": "input_ready", "forecast_input_id": input_id}


def resolve_forecast_context(forecast_id: str = "latest", time_h: float | None = None,
                             view: str = "current") -> dict:
    if view == "current" and time_h not in (None, ""):
        view = "time_slice"
    result = forecast_input_context()
    result.update({"view": view, "forecast_id": forecast_id})
    if not result["available"]:
        return result
    result["available"] = False
    if view not in {"current", "time_slice", "envelope"}:
        return {**result, "status": "invalid_view", "reason": "view 必须为 current、time_slice 或 envelope。"}
    metadata = forecast_metadata(forecast_id)
    if not metadata or metadata.get("status") != "completed":
        return {**result, "status": "no_forecast", "reason": "当前演示尚无已完成预测。"}
    result.update({"forecast_version": metadata.get("forecast_id"), "valid_from": metadata.get("valid_from"),
                   "valid_to": metadata.get("valid_to"), "generated_at": metadata.get("generated_at")})
    if metadata.get("workspace_id") != result["workspace_id"]:
        return {**result, "status": "workspace_mismatch", "reason": "预测不属于当前演示。"}
    input_id = metadata.get("forecast_input_id")
    if not input_id:
        raw = metadata.get("boundary_flow") or {}
        try:
            boundary = json.loads(raw) if isinstance(raw, str) else raw
            input_id = boundary.get("boundary_flow_id")
        except (ValueError, AttributeError):
            input_id = None
    if input_id != result["forecast_input_id"]:
        return {**result, "status": "stale_input", "reason": "预测输入已更新，旧预测不能作为当前态势依据。"}
    start, end, current = _time(result["valid_from"]), _time(result["valid_to"]), _time(result["simulation_time"])
    if not start or not end or not start <= current <= end:
        return {**result, "status": "outside_forecast_window", "reason": "当前演进时刻不在预测有效时段内。"}
    if not forecast_depth_path(forecast_id).is_file():
        return {**result, "status": "missing_forecast_data", "reason": "预测结果文件缺失。"}
    if view == "envelope":
        if time_h not in (None, ""):
            return {**result, "status": "invalid_time", "reason": "最大包络不能同时指定时间切片。"}
        return {**result, "status": "available", "available": True, "time_h": None,
                "analysis_time_at": None, "basis": "整个预测时段的最大淹没包络，不代表当前已经淹没。"}
    if time_h in (None, ""):
        if view == "time_slice":
            return {**result, "status": "invalid_time", "reason": "time_slice 必须指定 time_h。"}
        requested = (current - start).total_seconds() / 3600
    else:
        try:
            requested = float(time_h)
        except (TypeError, ValueError):
            requested = math.nan
    hours = forecast_time_steps(forecast_id)
    if not math.isfinite(requested) or not hours or not forecast_series_path(forecast_id).is_file() or not min(hours) <= requested <= max(hours):
        return {**result, "status": "time_unavailable", "reason": "该时刻没有可用预测切片，不能以未来最大包络代替。"}
    actual = min(hours, key=lambda hour: abs(hour - requested))
    return {**result, "status": "available", "available": True, "time_h": actual,
            "requested_time_h": requested, "analysis_time_at": offset_time_iso(result["valid_from"], actual),
            "basis": "对应时刻的模型预测切片，不是现场实测淹水。"}


def get_flood_status(view: str = "current", time_h: float | None = None) -> dict:
    initial = initial_flood_context()
    if initial and view == "current" and time_h in (None, ""):
        return {**initial, "has_inundation": False, "flooded_count": 0, "max_depth_m": 0.0}
    context = resolve_forecast_context(time_h=time_h, view=view)
    if not context["available"]:
        next_step = (
            "请先在态势工作台点击开始演进，等待当前输入生成预测后再查询。"
            if context["status"] in {"not_started", "no_current_input"}
            else "当前没有适用预测，请等待当前轮次的有效预测结果；不要使用旧预测替代。"
        )
        return {**context, "has_inundation": None, "next_step": next_step}
    entry = forecast_depth_entry("latest", time_h=context["time_h"])
    return {**context, "has_inundation": bool(entry["flooded_count"]),
            "flooded_count": entry["flooded_count"], "max_depth_m": round(entry["max_depth_m"], 4)}


def unavailable_forecast(context: dict) -> dict:
    return {"status": "forecast_unavailable", "error": context["reason"], "retryable": False,
            "forecast_context": context}
