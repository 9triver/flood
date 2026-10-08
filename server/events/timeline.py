"""Persistence for the event timeline emitted by the runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EventTimelineStore:
    """Append runtime output items to a workspace timeline.

    Timeline persistence is deliberately best effort: the live SSE stream
    must continue even when the local workspace cannot be written.
    """

    def append(self, item: dict[str, Any], workspace_id: str,
               workspaces: Any) -> None:
        if not workspace_id:
            return
        path = (
            workspaces.path(str(workspace_id), create=True)
            / "events"
            / "timeline.jsonl"
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False, default=str))
                stream.write("\n")
        except OSError:
            return


__all__ = ["EventTimelineStore"]
