from __future__ import annotations

import json
from typing import Any

from oag.ontology.schema import Ontology


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
