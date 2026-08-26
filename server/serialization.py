from __future__ import annotations

import json
from typing import Any


def format_sse(
    event: str,
    data: dict[str, Any],
    *,
    event_id: str | None = None,
) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event}\ndata: {payload}\n\n".encode("utf-8")


def parse_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
