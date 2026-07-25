from __future__ import annotations

import json
from typing import Any

from oag.ontology.schema import Ontology


READ_ONLY_AGENT_TOOLS = frozenset({
    "inspect",
    "query",
    "count",
    "query_links",
    "describe",
    "pivot",
    "distribution",
    "search",
    "read_tool_result",
})


def select_user_agent_tools(message: str, ontology: Ontology,
                            recent_context: str = "") -> frozenset[str]:
    text = str(message or "").lower()
    context_text = str(recent_context or "").lower()

    selected = set(READ_ONLY_AGENT_TOOLS)
    policy = ontology.interaction_policies.get("user_chat")
    for intent in (policy.intents.values() if policy else []):
        direct_match = any(
            keyword.lower() in text for keyword in intent.keywords
        )
        context_match = any(
            keyword.lower() in context_text
            for keyword in intent.context_keywords
        )
        if direct_match or context_match:
            selected.update(intent.tools)
    return frozenset(selected)


def build_agent_task_hint(message: str, ontology: Ontology) -> str:
    text = str(message or "")
    policy = ontology.interaction_policies.get("user_chat")
    count_intent = policy.intents.get("count") if policy else None
    if not count_intent or not any(
        keyword in text for keyword in count_intent.keywords
    ):
        return ""

    calls = []
    seen = set()
    for object_type, object_def in ontology.objects.items():
        if not object_def.countable:
            continue
        for alias in object_def.aliases:
            if not any(term in text for term in alias.terms):
                continue
            key = (object_type, json.dumps(alias.filters, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            args: dict[str, Any] = {"object_type": object_type}
            if alias.filters:
                args["filters"] = alias.filters
            calls.append(f"count({json.dumps(args, ensure_ascii=False)})")
    if not calls:
        return ""
    return count_intent.task_hint.format(calls="、".join(calls))
