"""Freeze the dialogue's map time; automatic events retain runtime clock semantics."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
import math

from oag.runtime.hooks import HookResult
from domains.flood.runtime.forecast_context import resolve_forecast_context, initial_flood_context
from domains.flood.runtime.hydrodynamic_grid import forecast_metadata, offset_time_iso
from domains.flood.runtime.workspace import active_workspace_id


SLICE_TOOLS = frozenset({"get_flood_status", "analyze_inundation_impacts", "plan_route",
                         "compare_evacuation_sites", "review_route"})
FORECAST_TOOLS = SLICE_TOOLS | {"analyze_latest_evacuation_time", "assess_flood_emergency"}
LATEST_IDS = {"", "latest", "forecast_latest"}
_ANALYSIS: ContextVar["ChatAnalysisContext | None"] = ContextVar("flood_chat_analysis", default=None)


def _offset(at, start):
    try:
        return (datetime.fromisoformat(str(at).replace("Z", "+00:00"))
                - datetime.fromisoformat(str(start).replace("Z", "+00:00"))).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class ChatAnalysisContext:
    workspace_id: str
    forecast_id: str
    source: str
    time_h: float | None
    simulation_time_h: float | None
    initial: bool
    error: str
    default_error: str
    forecast: dict

    def prompt_context(self) -> dict:
        return {
            "source": self.source, "workspace_id": self.workspace_id,
            "forecast_id": self.forecast_id, "time_h": self.time_h,
            "analysis_time_at": offset_time_iso(self.forecast.get("valid_from"), self.time_h),
            "simulation_time": self.forecast.get("simulation_time"),
            "simulation_time_h": self.simulation_time_h,
            "initial_dry": self.initial, "error": self.error or self.default_error,
            "forecast_validity": self.forecast,
            "rule": "现在/当前默认指本轮冻结的分析时刻。明确指定时刻可用 time_slice/time_h 覆盖；"
                    "明确问演进当前时刻用 simulation_current；明确问整个预测期或最不利情况用 envelope。"
                    "回复注明实际分析日期时间及预测 +Nh；最大包络没有单一分析时刻。"
                    "工具不可用时说明原因，不得切换预测版本、切片或包络替代。",
        }


def capture_analysis_context(selected: dict) -> ChatAnalysisContext:
    wid = active_workspace_id()
    timeline = selected.get("hydrodynamic_timeline") or {}
    active = bool(timeline.get("active")) and timeline.get("mode") in {"time_slice", "envelope"}
    uses_slice = active and timeline.get("mode") == "time_slice"
    error = ""
    if selected.get("workspace_id") and selected["workspace_id"] != wid:
        error = "提问时的地图属于其他工作空间，请刷新地图后重新提问。"
    version = str(timeline.get("forecast_version") or "") if active else ""
    if active and (version in LATEST_IDS or not version):
        error = error or "地图预测版本缺失，请重新加载预测时间轴后提问。"
    if not active:
        version = str(forecast_metadata().get("forecast_id") or "")
    # Never leave a mutable 'latest' alias in an in-flight dialogue.
    if version in LATEST_IDS:
        version = ""
    initial_context = initial_flood_context() if not active else None
    initial = bool(initial_context)
    if initial:
        version = ""
    forecast = initial_context or resolve_forecast_context(version or "latest", view="envelope")
    if not initial and not version:
        error = error or "提问时没有可锁定的有效预测版本，请等待预测完成后重新提问。"
    simulation_h = _offset(forecast.get("simulation_time"), forecast.get("valid_from"))
    hour = simulation_h
    default_error = ""
    if uses_slice:
        raw = timeline.get("current_hydrodynamic_time_h")
        try:
            hour = float(raw) if not isinstance(raw, bool) else math.nan
        except (ValueError, TypeError):
            hour = math.nan
        if not math.isfinite(hour):
            default_error = "时间轴选中时刻无效，请重新选择预测帧后提问。"
            hour = None
        valid_at = timeline.get("current_hydrodynamic_valid_at")
        if valid_at and hour is not None:
            actual_h = _offset(valid_at, forecast.get("valid_from"))
            if actual_h is None or not math.isclose(actual_h, hour, abs_tol=1e-6):
                error = error or "地图时刻与预测版本不一致，请重新加载预测时间轴。"
    if not initial:
        forecast = resolve_forecast_context(version or "latest", hour, "time_slice")
    return ChatAnalysisContext(wid, version, "map_timeline" if uses_slice else "simulation_clock",
                               hour, simulation_h, initial, error, default_error, forecast)


@contextmanager
def analysis_scope(context: ChatAnalysisContext):
    token = _ANALYSIS.set(context)
    try:
        yield
    finally:
        _ANALYSIS.reset(token)


def normalize_analysis_tool(context: dict) -> HookResult:
    """Runs before tool caching; contextvars also propagate to parallel tool workers."""
    analysis = _ANALYSIS.get()
    name = context.get("tool_name")
    if analysis is None or name not in FORECAST_TOOLS or str(context.get("session_id", "")).startswith("event-"):
        return HookResult()
    args = context["args"]
    if analysis.error:
        return HookResult(action="block", reason=analysis.error)
    if active_workspace_id() != analysis.workspace_id:
        return HookResult(action="block", reason="本轮分析工作空间已变化，请重新提问。")
    if analysis.initial and not initial_flood_context():
        return HookResult(action="block", reason="提问后演进已开始，请重新提问以使用新的态势。")
    version = args.get("forecast_id") or "latest"
    if version not in LATEST_IDS and version != analysis.forecast_id:
        return HookResult(action="block", reason="本轮已锁定预测版本，请使用本轮 forecast_id；切换版本后需重新提问。")
    args["forecast_id"] = analysis.forecast_id or "latest"
    if name in SLICE_TOOLS:
        view = args.get("view", "current")
        hour = args.get("time_h")
        if view == "simulation_current":
            if hour not in (None, ""):
                return HookResult(action="block", reason="simulation_current 不能同时指定 time_h。")
            hour = analysis.simulation_time_h
            view = "current" if analysis.initial else "time_slice"
        elif view == "current" and hour in (None, ""):
            if analysis.default_error:
                return HookResult(action="block", reason=analysis.default_error)
            hour = analysis.time_h
            view = "current" if analysis.initial else "time_slice"
        elif view == "current":
            view = "time_slice"
        args["view"] = view
        if hour not in (None, ""):
            args["time_h"] = hour
        if analysis.initial and view == "current" and hour in (None, ""):
            # The unstarted dry-state contract belongs to status/routing tools only.
            return HookResult()
    else:
        view, hour = "envelope", None
    validated = resolve_forecast_context(args["forecast_id"], hour, view)
    if not validated["available"]:
        return HookResult(action="block", reason=validated["reason"])
    return HookResult()
