from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from domains.flood.runtime.workspace import WORKSPACES, WorkspaceManager


DIRECTIVE_PRIORITIES = frozenset({"normal", "urgent", "critical"})
_DIRECTIVE_ID_PATTERN = re.compile(r"^DIR-(\d{8})-(\d{3,})$")


class DirectiveStore:
    """Append-only issued directives scoped to one evolution workspace."""

    def __init__(self, workspaces: WorkspaceManager = WORKSPACES):
        self.workspaces = workspaces
        self._lock = threading.RLock()

    def list_issued(self) -> dict[str, Any]:
        workspace_id = self.workspaces.active_id
        if not workspace_id:
            return {"workspace_id": None, "directives": []}
        with self._lock:
            directives = self._read_records(self._issued_path(workspace_id))
        return {
            "workspace_id": workspace_id,
            "directives": list(reversed(directives)),
        }

    def issue(
        self,
        payload: dict[str, Any],
        runtime_status: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_id = self.workspaces.active_id
        if not workspace_id:
            raise ValueError("当前没有演进工作空间，无法发出应急指令")
        expected_workspace_id = str(payload.get("workspace_id") or "").strip()
        if expected_workspace_id and expected_workspace_id != workspace_id:
            raise ValueError("演进工作空间已切换，请重新检查指令初稿")

        title = _required_text(payload, "title", 200)
        content = _required_text(payload, "content", 20_000)
        recipients = _required_text(payload, "recipients", 500)
        priority = str(payload.get("priority") or "urgent").strip().lower()
        if priority not in DIRECTIVE_PRIORITIES:
            raise ValueError("priority 必须是 normal、urgent 或 critical")

        now = datetime.now().astimezone()
        issued_path = self._issued_path(workspace_id)
        with self._lock:
            records = self._read_records(issued_path)
            record = {
                "directive_id": self._next_id(records, now),
                "workspace_id": workspace_id,
                "title": title,
                "content": content,
                "recipients": recipients,
                "priority": priority,
                "status": "issued",
                "simulation_time": runtime_status.get("observed_at"),
                "forecast_version": runtime_status.get("forecast_version"),
                "issued_at": now.isoformat(timespec="seconds"),
            }
            issued_path.parent.mkdir(parents=True, exist_ok=True)
            with issued_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def _issued_path(self, workspace_id: str) -> Path:
        return self.workspaces.path(workspace_id) / "directives" / "issued.jsonl"

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
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

    @staticmethod
    def _next_id(records: list[dict[str, Any]], now: datetime) -> str:
        date = now.strftime("%Y%m%d")
        highest = 0
        for record in records:
            match = _DIRECTIVE_ID_PATTERN.match(
                str(record.get("directive_id") or "")
            )
            if match and match.group(1) == date:
                highest = max(highest, int(match.group(2)))
        return f"DIR-{date}-{highest + 1:03d}"


def _required_text(values: dict[str, Any], key: str, limit: int) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} 不能为空")
    if len(value) > limit:
        raise ValueError(f"{key} 不能超过 {limit} 个字符")
    return value
