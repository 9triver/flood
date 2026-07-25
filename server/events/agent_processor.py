from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from oag.runtime.events import event_to_dict

from domains.flood.runtime.workspace import active_workspace_id, workspace_scope
from server.events.factory import (
    event_forecast_input_id,
    forecast_completed,
    make_impact_event,
    make_inundation_event,
)
from server.events.messages import (
    boundary_flow_observation_detail,
    compact_event_text,
    impact_event_detail,
    is_impact_result,
    readable_event_tool,
)
from server.presentation.event_maps import filter_event_map_event
from server.serialization import parse_json_object


AppendOutput = Callable[[str, dict[str, Any], int | None], None]
PublishEvent = Callable[[dict[str, Any], int], None]


class ForecastPlaybackTracker(Protocol):
    def mark_forecast_started(self, forecast_input_id: str) -> bool: ...

    def mark_forecast_completed(self, forecast_input_id: str) -> bool: ...

    def mark_forecast_failed(self, forecast_input_id: str) -> bool: ...


class AgentSideEffectReader(Protocol):
    def pop_map_events(self, session_id: str) -> list[dict[str, Any]]: ...

    def pop_forecast_results(self, session_id: str) -> list[dict[str, Any]]: ...

    def pop_impact_results(self, session_id: str) -> list[dict[str, Any]]: ...


class EventAgentProcessor:
    """Run ontology-driven agents for queued domain events.

    Runtime concurrency and event publication remain owned by ``EventRuntime``;
    this processor only interprets an event, invokes the agent, and reports
    resulting traces or child events through callbacks.
    """

    def __init__(
        self,
        *,
        app: Any,
        playback_runner: ForecastPlaybackTracker,
        current_generation: Callable[[], int],
        append_output: AppendOutput,
        publish_inundation_event: PublishEvent,
        publish_impact_event: PublishEvent,
        side_effects: AgentSideEffectReader | None = None,
    ):
        self.app = app
        self.playback_runner = playback_runner
        self.current_generation = current_generation
        self.append_output = append_output
        self.publish_inundation_event = publish_inundation_event
        self.publish_impact_event = publish_impact_event
        self.side_effects = side_effects

    def handle_event(self, event: dict[str, Any], generation: int) -> None:
        workspace_id = str(
            event.get("workspace_id") or active_workspace_id() or ""
        )
        with workspace_scope(workspace_id or None):
            if not self._is_current(generation):
                return
            if event.get("event_type") == "FloodForecastRequired":
                self._handle_forecast_required(event, generation)
                return
            if event.get("event_type") == "InundationGenerated":
                self._run_agent_for_inundation_event(event, generation)

    def _handle_forecast_required(self, event: dict[str, Any],
                                  generation: int) -> None:
        agent_result = self._run_agent_for_forecast_required_event(
            event, generation,
        )
        trace = self._reason_about_forecast_required_event(event)
        self.append_output("agent_trace", trace, generation)
        forecast_result = agent_result.get("forecast_result")
        if not forecast_result and agent_result.get("forecast_requested"):
            forecast_result = self.app.forecast(force=False)
        if forecast_result:
            self._record_forecast_policy_result(
                event, forecast_result, agent_result,
            )
        if (
            forecast_completed(forecast_result)
            and not agent_result.get("forecast_event_published")
        ):
            inundation_event = make_inundation_event(
                event,
                forecast_result,
                str(trace.get("severity") or "warning"),
            )
            self.publish_inundation_event(inundation_event, generation)
            agent_result["forecast_event_published"] = True

    def _run_agent_for_forecast_required_event(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        policy = self.app.ontology.event_policies["FloodForecastRequired"]
        prompt = self.app.agent.harness.ont.build_event_prompt(
            "FloodForecastRequired", event,
        )
        agent_result: dict[str, Any] = {}
        reasoning_chunks: list[str] = []
        text_chunks: list[str] = []
        try:
            for raw_event in self.app.agent.chat_stream(
                prompt,
                session_id=session_id,
                allowed_tools=frozenset(policy.allowed_tools),
            ):
                if not self._is_current(generation):
                    return agent_result
                self._collect_agent_side_effects(
                    session_id, agent_result, generation,
                )
                self._publish_forecast_result_from_agent(
                    event, agent_result, generation,
                )
                data = event_to_dict(raw_event)
                event_type = data.get("type")
                if event_type == "tool_call":
                    tool_name = str(data.get("name") or "")
                    if tool_name == "run_flood_forecast":
                        agent_result["forecast_requested"] = True
                        self.playback_runner.mark_forecast_started(
                            str(event.get("source_id") or "")
                        )
                    self._append_tool_trace("CALL", tool_name, data, generation)
                elif event_type == "tool_result":
                    tool_name = str(data.get("name") or "")
                    self._append_tool_trace("RESULT", tool_name, data, generation)
                    if tool_name == "run_flood_forecast":
                        agent_result["forecast_requested"] = True
                        parsed = parse_json_object(data.get("result") or "")
                        if parsed and "error" not in parsed:
                            agent_result["forecast_result"] = parsed
                        elif not data.get("blocked"):
                            agent_result["forecast_result"] = self.app.forecast(
                                force=False,
                            )
                        self._publish_forecast_result_from_agent(
                            event, agent_result, generation,
                        )
                elif event_type == "reasoning":
                    reasoning_chunks.append(str(data.get("content") or ""))
                elif event_type == "text":
                    text_chunks.append(str(data.get("content") or ""))
            self._collect_agent_side_effects(
                session_id, agent_result, generation,
            )
            if (
                agent_result.get("forecast_requested")
                and not agent_result.get("forecast_result")
            ):
                agent_result["forecast_result"] = self.app.forecast(force=False)
            self._publish_forecast_result_from_agent(
                event, agent_result, generation,
            )
            self._append_agent_text_traces(
                reasoning_chunks, text_chunks, generation,
            )
        except Exception as exc:
            self.append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "FALLBACK",
                "label": "LLM 事件推理失败，启用规则兜底",
                "detail": str(exc),
            }, generation)
        return agent_result

    def _run_agent_for_inundation_event(
        self,
        event: dict[str, Any],
        generation: int,
    ) -> dict[str, Any]:
        if not self.app.agent:
            return {}
        session_id = f"event-{event['event_id']}"
        policy = self.app.ontology.event_policies["InundationGenerated"]
        prompt = self.app.agent.harness.ont.build_event_prompt(
            "InundationGenerated", event,
        )
        return self._run_agent_for_followup_event(
            prompt,
            session_id,
            generation,
            allowed_tools=frozenset(policy.allowed_tools),
            fallback_label="LLM 淹没事件推理失败",
            map_event_filter=lambda map_event: filter_event_map_event(
                map_event, policy.automatic_map,
            ),
        )

    def _publish_forecast_result_from_agent(
        self,
        source_event: dict[str, Any],
        agent_result: dict[str, Any],
        generation: int,
    ) -> None:
        forecast_result = agent_result.get("forecast_result")
        if not forecast_result or agent_result.get("forecast_event_published"):
            return
        self._record_forecast_policy_result(
            source_event, forecast_result, agent_result,
        )
        if not forecast_completed(forecast_result):
            return
        trace = self._reason_about_forecast_required_event(source_event)
        inundation_event = make_inundation_event(
            source_event,
            forecast_result,
            str(trace.get("severity") or "warning"),
        )
        self.publish_inundation_event(inundation_event, generation)
        agent_result["forecast_event_published"] = True

    def _record_forecast_policy_result(
        self,
        source_event: dict[str, Any],
        forecast_result: dict[str, Any],
        agent_result: dict[str, Any],
    ) -> None:
        if agent_result.get("forecast_policy_recorded"):
            return
        forecast = forecast_result.get("forecast") or {}
        status = str(forecast.get("status") or "")
        forecast_input_id = event_forecast_input_id(source_event)
        if status == "completed":
            self.playback_runner.mark_forecast_completed(forecast_input_id)
            agent_result["forecast_policy_recorded"] = True
        elif status == "failed":
            self.playback_runner.mark_forecast_failed(forecast_input_id)
            agent_result["forecast_policy_recorded"] = True

    def _run_agent_for_followup_event(
        self,
        prompt: str,
        session_id: str,
        generation: int,
        allowed_tools: frozenset[str],
        fallback_label: str,
        map_event_filter: (
            Callable[[dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        reasoning_chunks: list[str] = []
        text_chunks: list[str] = []
        try:
            for raw_event in self.app.agent.chat_stream(
                prompt,
                session_id=session_id,
                allowed_tools=allowed_tools,
            ):
                if not self._is_current(generation):
                    return result
                self._collect_agent_side_effects(
                    session_id,
                    result,
                    generation,
                    map_event_filter=map_event_filter,
                )
                data = event_to_dict(raw_event)
                event_type = data.get("type")
                if event_type == "tool_call":
                    self._append_tool_trace(
                        "CALL", str(data.get("name") or ""), data, generation,
                    )
                elif event_type == "tool_result":
                    tool_name = str(data.get("name") or "")
                    parsed = parse_json_object(data.get("result") or "")
                    if (
                        tool_name == "analyze_inundation_impacts"
                        and is_impact_result(parsed)
                    ):
                        result["impact_result"] = parsed
                        self.publish_impact_event(
                            make_impact_event(parsed, session_id), generation,
                        )
                    self._append_tool_trace(
                        "RESULT", tool_name, data, generation,
                    )
                elif event_type == "reasoning":
                    reasoning_chunks.append(str(data.get("content") or ""))
                elif event_type == "text":
                    text_chunks.append(str(data.get("content") or ""))
            self._collect_agent_side_effects(
                session_id,
                result,
                generation,
                map_event_filter=map_event_filter,
            )
            self._append_agent_text_traces(
                reasoning_chunks, text_chunks, generation,
            )
            self._append_followup_complete_trace(result, generation)
        except Exception as exc:
            self.append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "FALLBACK",
                "label": fallback_label,
                "detail": str(exc),
            }, generation)
        return result

    def _collect_agent_side_effects(
        self,
        session_id: str,
        result: dict[str, Any],
        generation: int,
        map_event_filter: (
            Callable[[dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
    ) -> None:
        if not self.side_effects:
            return
        for forecast_result in self.side_effects.pop_forecast_results(session_id):
            result["forecast_result"] = forecast_result
        for impact_result in self.side_effects.pop_impact_results(session_id):
            result["impact_result"] = impact_result
            self.publish_impact_event(
                make_impact_event(impact_result, session_id), generation,
            )
        for map_event in self.side_effects.pop_map_events(session_id):
            filtered_event = (
                map_event_filter(map_event) if map_event_filter else map_event
            )
            if filtered_event:
                self.append_output("map_actions", filtered_event, generation)

    def _append_tool_trace(self, tag: str, tool_name: str,
                           data: dict[str, Any], generation: int) -> None:
        detail = (
            json.dumps(data.get("args") or {}, ensure_ascii=False)
            if tag == "CALL"
            else compact_event_text(data.get("result") or "")
        )
        self.append_output("agent_trace", {
            "type": "agent_trace",
            "tag": tag,
            "label": readable_event_tool(tool_name),
            "detail": detail,
        }, generation)

    def _append_agent_text_traces(self, reasoning_chunks: list[str],
                                  text_chunks: list[str],
                                  generation: int) -> None:
        reasoning = "".join(reasoning_chunks).strip()
        if reasoning:
            self.append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "THINK",
                "label": "LLM 事件推理",
                "detail": compact_event_text(reasoning),
            }, generation)
        conclusion = "".join(text_chunks).strip()
        if conclusion:
            self.append_output("agent_trace", {
                "type": "agent_trace",
                "tag": "TEXT",
                "label": "智能体结论",
                "detail": compact_event_text(conclusion, limit=1800),
            }, generation)

    def _append_followup_complete_trace(self, result: dict[str, Any],
                                        generation: int) -> None:
        impact_result = result.get("impact_result")
        detail = (
            impact_event_detail({"payload": impact_result})
            if is_impact_result(impact_result)
            else "事件智能体处理已结束。"
        )
        self.append_output("agent_trace", {
            "type": "agent_trace",
            "tag": "DONE",
            "label": "事件处理完成",
            "detail": detail,
        }, generation)

    def _reason_about_forecast_required_event(
        self,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        payload = event.get("payload") or {}
        trigger = payload.get("forecast_trigger") or {}
        observation = payload.get("observation") or {}
        should_run = bool(trigger.get("should_run_forecast"))
        detail = (
            "智能体接收洪水预测请求。"
            if self.app.agent
            else "未启用 LLM，洪水预测请求保持待处理。"
        )
        return {
            "type": "agent_trace",
            "tag": "SYSTEM",
            "label": "洪水预测请求",
            "detail": (
                f"{detail} {boundary_flow_observation_detail(observation)} "
                f"{trigger.get('reason', '')}"
            ),
            "should_run_model": should_run,
            "severity": event.get("severity", "warning"),
        }

    def _is_current(self, generation: int) -> bool:
        return generation == self.current_generation()
