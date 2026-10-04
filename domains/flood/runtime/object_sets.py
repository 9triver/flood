"""Immutable query results used across chat turns in one workspace."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from .common import apply_filters, id_field
from .workspace import active_workspace_id, workspace_dir


_SET_ID = re.compile(r"^set_[0-9a-f]{32}$")


def save_object_set(object_type: str, object_ids: list[str], *, basis: dict | None = None,
                    parent_set_id: str | None = None) -> dict:
    if not active_workspace_id():
        raise ValueError("当前没有工作空间，无法保存对象集合")
    record = {"object_set_id": f"set_{uuid.uuid4().hex}", "workspace_id": active_workspace_id(),
              "object_type": object_type, "object_ids": list(dict.fromkeys(map(str, object_ids))),
              "basis": basis or {}, "parent_set_id": parent_set_id,
              "created_at": datetime.now(timezone.utc).isoformat()}
    directory = workspace_dir() / "object_sets"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record['object_set_id']}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def read_object_set(object_set_id: str, object_type: str | None = None) -> dict:
    if not isinstance(object_set_id, str) or not _SET_ID.fullmatch(object_set_id):
        raise ValueError("对象集合 ID 无效")
    try:
        record = json.loads((workspace_dir() / "object_sets" / f"{object_set_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("当前工作空间中没有该对象集合，请重新查询") from exc
    if record.get("workspace_id") != active_workspace_id() or (object_type and record.get("object_type") != object_type):
        raise ValueError("对象集合的工作空间或类型不匹配")
    return record


def set_summary(record: dict) -> dict:
    return {"object_set_id": record["object_set_id"], "object_type": record["object_type"],
            "count": len(record["object_ids"]), "parent_set_id": record.get("parent_set_id"),
            "basis": record.get("basis", {})}


def refine_object_set(resolver, object_set_id: str, filters: dict | None = None,
                      exclude_object_ids: list[str] | None = None) -> dict:
    try:
        source = read_object_set(object_set_id)
        if not isinstance(filters or {}, dict):
            raise ValueError("filters 必须是对象属性条件")
        if exclude_object_ids is not None and (not isinstance(exclude_object_ids, list) or any(not isinstance(value, str) for value in exclude_object_ids)):
            raise ValueError("exclude_object_ids 必须是 ID 列表")
        kind = source["object_type"]
        field = id_field(kind)
        rows = resolver.query(kind)
        fields = {key for row in rows for key in row}
        if rows and any(key.split("__", 1)[0] not in fields for key in (filters or {})):
            raise ValueError("过滤字段不属于该对象类型")
        selected = set(source["object_ids"]) - set(exclude_object_ids or [])
        rows = apply_filters([row for row in rows if str(row.get(field)) in selected], filters)
        record = save_object_set(kind, [str(row[field]) for row in rows],
                                 parent_set_id=source["object_set_id"],
                                 basis={"source": source.get("basis", {}), "filters": filters or {},
                                        "excluded_object_ids": exclude_object_ids or []})
        return {"status": "completed", **set_summary(record),
                "map_update": {"object_type": kind, "object_set_id": record["object_set_id"], "replace_object_set_id": object_set_id},
                "object_ids": record["object_ids"][:20],
                "preview_only": len(record["object_ids"]) > 20}
    except (TypeError, ValueError) as exc:
        return {"error": str(exc)}
