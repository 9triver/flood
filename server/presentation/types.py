from __future__ import annotations

from typing import Any, TypedDict


class MapAction(TypedDict, total=False):
    type: str
    object_type: str
    object_id: str
    object_ids: list[str]
    filters: dict[str, Any]
    label: str
    fit: bool
    refresh: bool
    replace_object_type: bool
    simplify_tolerance: float
    mesh_only: bool
    event: dict[str, Any]


class ResultCard(TypedDict):
    title: str
    value: str
    detail: str


class FrontendMapPayload(TypedDict):
    kind: str
    context: str
    map_actions: list[MapAction]
    result_cards: list[ResultCard]
    note: str
