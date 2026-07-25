from __future__ import annotations

from typing import Any

from oag.ontology.schema import Ontology, PresentationToolDef
from oag.tools.registry import ToolPolicy


def presentation_tool(ontology: Ontology, name: str) -> PresentationToolDef:
    tool = ontology.presentation_tools.get(name)
    if not tool:
        raise ValueError(f"ontology presentation tool not found: {name}")
    return tool


def presentation_tool_kwargs(
    ontology: Ontology,
    name: str,
    **prompt_context: str,
) -> dict[str, Any]:
    definition = presentation_tool(ontology, name)
    usage_prompt = definition.usage_prompt
    for key, value in prompt_context.items():
        usage_prompt = usage_prompt.replace("{" + key + "}", value)
    return {
        "description": definition.description or definition.summary,
        "usage_prompt": usage_prompt,
        "category": definition.category,
        "policy": ToolPolicy(
            read_only=definition.side_effect_scope == "none",
            requires_confirmation=definition.requires_confirmation,
            concurrency_safe=definition.concurrency_safe,
            worker_allowed=definition.worker_allowed,
            idempotent=definition.idempotent,
            destructive=definition.destructive,
            timeout_seconds=definition.timeout_seconds,
        ),
    }
