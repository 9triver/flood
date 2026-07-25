from __future__ import annotations

import json
import threading
from collections.abc import Collection
from typing import Any

from oag.runtime.hooks import HookResult

from server.presentation.directive_tools import tool_result_to_directive_event
from server.presentation.map_actions import tool_result_to_map_event
from server.serialization import parse_json_object


class AgentSideEffects:
    """Collect tool results that must be consumed outside the Agent loop."""

    def __init__(self, presentation_tools: Collection[str]):
        self.presentation_tools = frozenset(presentation_tools)
        self._map_events: dict[str, list[dict[str, Any]]] = {}
        self._map_events_lock = threading.Lock()
        self._directive_events: dict[str, list[dict[str, Any]]] = {}
        self._directive_events_lock = threading.Lock()
        self._event_tool_results: dict[str, list[dict[str, Any]]] = {}
        self._event_tool_results_lock = threading.Lock()
        self._forecast_results: dict[str, list[dict[str, Any]]] = {}
        self._forecast_results_lock = threading.Lock()
        self._impact_results: dict[str, list[dict[str, Any]]] = {}
        self._impact_results_lock = threading.Lock()

    def capture_tool_event(self, context: dict[str, Any]) -> HookResult:
        tool_name = str(context.get("tool_name") or "")
        session_id = str(context.get("session_id") or "")
        if session_id.startswith("event-") and tool_name:
            with self._event_tool_results_lock:
                self._event_tool_results.setdefault(session_id, []).append({
                    "tool_name": tool_name,
                    "result": context.get("result"),
                })
        if tool_name == "run_flood_forecast" and session_id:
            self._capture_json_result(
                self._forecast_results,
                self._forecast_results_lock,
                session_id,
                context.get("result"),
            )
            return HookResult(action="allow")
        if tool_name == "analyze_inundation_impacts" and session_id:
            self._capture_json_result(
                self._impact_results,
                self._impact_results_lock,
                session_id,
                context.get("result"),
            )
            return HookResult(action="allow")
        if tool_name not in self.presentation_tools:
            return HookResult(action="allow")

        event = tool_result_to_map_event(str(context.get("result") or ""))
        if event and session_id:
            self._queue_map_event(session_id, event)
            return HookResult(action="allow")
        directive_event = tool_result_to_directive_event(
            str(context.get("result") or "")
        )
        if directive_event and session_id:
            with self._directive_events_lock:
                self._directive_events.setdefault(session_id, []).append(
                    directive_event
                )
        return HookResult(action="allow")

    def pop_map_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._map_events_lock:
            return self._map_events.pop(session_id, [])

    def pop_directive_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._directive_events_lock:
            return self._directive_events.pop(session_id, [])

    def pop_event_tool_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._event_tool_results_lock:
            return self._event_tool_results.pop(session_id, [])

    def pop_forecast_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._forecast_results_lock:
            return self._forecast_results.pop(session_id, [])

    def pop_impact_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._impact_results_lock:
            return self._impact_results.pop(session_id, [])

    def _queue_map_event(self, session_id: str,
                         event: dict[str, Any]) -> None:
        signature = self._map_action_signature(event)
        with self._map_events_lock:
            queue = self._map_events.setdefault(session_id, [])
            if any(self._map_action_signature(item) == signature for item in queue):
                return
            queue.append(event)

    @staticmethod
    def _capture_json_result(
        store: dict[str, list[dict[str, Any]]],
        lock: threading.Lock,
        session_id: str,
        raw_result: Any,
    ) -> None:
        result = parse_json_object(raw_result or "")
        if not result or "error" in result:
            return
        with lock:
            store.setdefault(session_id, []).append(result)

    @staticmethod
    def _map_action_signature(event: dict[str, Any]) -> str:
        return json.dumps(
            event.get("map_actions", []),
            sort_keys=True,
            ensure_ascii=False,
        )
