from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import apply_filters, apply_order, apply_window
from .workspace import WORKSPACES, WorkspaceManager


def issued_directives_path(
    workspaces: WorkspaceManager | None = None,
    workspace_id: str | None = None,
) -> Path:
    workspaces = workspaces or WORKSPACES
    selected = workspace_id or workspaces.active_id
    if not selected:
        return workspaces.path("_inactive") / "directives" / "issued.jsonl"
    return workspaces.path(selected) / "directives" / "issued.jsonl"


def read_issued_directives(
    workspaces: WorkspaceManager | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    workspaces = workspaces or WORKSPACES
    path = issued_directives_path(workspaces, workspace_id)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def query_emergency_directives(
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
    order_by: str | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    rows = read_issued_directives()
    rows = apply_filters(rows, filters)
    rows = apply_order(rows, order_by)
    return apply_window(rows, limit, offset)


def count_emergency_directives(
    filters: dict[str, Any] | None = None,
) -> int:
    return len(query_emergency_directives(filters))
