from __future__ import annotations

import json
from typing import Any

from oag.ontology.schema import Ontology
from oag.tools.registry import ToolDef, ToolRegistry

from server.presentation.tool_metadata import presentation_tool_kwargs
from server.serialization import parse_json_object


DIRECTIVE_EDITOR_TOOL = "ui_open_emergency_directive_editor"
DIRECTIVE_PRIORITIES = frozenset({"normal", "urgent", "critical"})


def register_directive_tools(tools: ToolRegistry, ontology: Ontology) -> None:
    tools.register(ToolDef(
        name=DIRECTIVE_EDITOR_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "根据当前建议生成的简洁应急指令标题",
                },
                "content": {
                    "type": "string",
                    "description": "可供用户修改确认的完整指令正文",
                },
                "recipients": {
                    "type": "string",
                    "description": "指令接收单位或执行对象，多个对象用顿号分隔",
                },
                "priority": {
                    "type": "string",
                    "enum": sorted(DIRECTIVE_PRIORITIES),
                    "description": "普通 normal、紧急 urgent、特急 critical；默认 urgent",
                },
            },
            "required": ["title", "content", "recipients"],
        },
        handler=build_directive_editor_result,
        max_result_chars=16000,
        **presentation_tool_kwargs(ontology, DIRECTIVE_EDITOR_TOOL),
    ))


def build_directive_editor_result(args: dict[str, Any]) -> str:
    title = _required_text(args, "title", 200)
    content = _required_text(args, "content", 20_000)
    recipients = _required_text(args, "recipients", 500)
    priority = str(args.get("priority") or "urgent").strip().lower()
    if priority not in DIRECTIVE_PRIORITIES:
        priority = "urgent"
    return json.dumps({
        "kind": "frontend_directive_editor",
        "draft": {
            "title": title,
            "content": content,
            "recipients": recipients,
            "priority": priority,
        },
    }, ensure_ascii=False)


def tool_result_to_directive_event(result: str) -> dict[str, Any] | None:
    data = parse_json_object(result)
    if not data or data.get("kind") != "frontend_directive_editor":
        return None
    draft = data.get("draft")
    if not isinstance(draft, dict):
        return None
    try:
        return {
            "type": "directive_draft",
            "draft": {
                "title": _required_text(draft, "title", 200),
                "content": _required_text(draft, "content", 20_000),
                "recipients": _required_text(draft, "recipients", 500),
                "priority": _priority(draft.get("priority")),
            },
        }
    except ValueError:
        return None


def _required_text(values: dict[str, Any], key: str, limit: int) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    if len(value) > limit:
        raise ValueError(f"{key} exceeds {limit} characters")
    return value


def _priority(value: Any) -> str:
    priority = str(value or "urgent").strip().lower()
    return priority if priority in DIRECTIVE_PRIORITIES else "urgent"
