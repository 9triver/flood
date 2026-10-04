"""Capture the evidence a draft used; never silently rebase an old draft."""
from __future__ import annotations

from domains.flood.runtime import workspace
from domains.flood.runtime.workspace import active_workspace_id
from domains.flood.runtime.forecast_context import resolve_routing_context
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.object_sets import read_object_set
from domains.flood.runtime.evacuation_options import review_route


def current_basis_snapshot() -> dict:
    manifest = workspace.WORKSPACES.active_manifest() or {}
    context = resolve_routing_context()
    return {"workspace_id": active_workspace_id(), "simulation_time": manifest.get("simulation_time"),
            "forecast_version": context.get("forecast_version"),
            "forecast_input_id": context.get("forecast_input_id"), "flood_state": context.get("status")}


def build_directive_basis(route_id: str = "", object_set_id: str = "") -> dict:
    review = None
    if object_set_id:
        read_object_set(object_set_id)
    if route_id:
        review = review_route(FloodRepository(), route_id)
        if review.get("passable") is not True:
            raise ValueError("该路线未通过当前洪水状态复核，不能用于转移指令草稿")
    return {"snapshot": current_basis_snapshot(), "evacuation_route_id": route_id or None,
            "object_set_id": object_set_id or None, "route_review": review}


def validate_directive_basis(basis: dict) -> None:
    if not isinstance(basis, dict) or basis.get("snapshot") != current_basis_snapshot():
        raise ValueError("草稿依据的演进时刻或预测已变化，请重新复核并生成草稿")
    if basis.get("object_set_id"):
        read_object_set(basis["object_set_id"])
    if basis.get("evacuation_route_id"):
        result = review_route(FloodRepository(), basis["evacuation_route_id"])
        if result.get("passable") is not True:
            raise ValueError("草稿路线未通过当前洪水状态复核，请重新调整处置方案")
