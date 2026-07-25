from __future__ import annotations

from typing import Any

from oag.ontology.schema import EventMapPolicyDef


def filter_event_map_event(event: dict[str, Any],
                           policy: EventMapPolicyDef) -> dict[str, Any] | None:
    allowed_types = set(policy.allowed_action_types)
    actions = [
        action for action in event.get("map_actions") or []
        if isinstance(action, dict)
        and action.get("type") in allowed_types
    ]
    if not actions:
        return None
    return {
        **event,
        "map_actions": actions,
        "result_cards": (
            event.get("result_cards", [])
            if policy.include_result_cards
            else []
        ),
    }
