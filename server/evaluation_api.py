from __future__ import annotations

import inspect
import time
import uuid
from typing import Any, Iterable

from oag.runtime.events import (
    ConfirmationEvent,
    HookBlockedEvent,
    QuestionEvent,
    TextEvent,
    ToolResultEvent,
)


class EvaluationApiError(Exception):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def build_chat_completion_response(app: Any, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Run the domain agent through an OpenAI Chat Completions-compatible API.

    The official dynamic-evaluation-runner calls a plain HTTP JSON endpoint,
    not the frontend SSE endpoint.  This adapter keeps the frontend untouched
    while exposing the same OAG agent run and the generated GenAI trace id.
    """

    if not isinstance(payload, dict):
        raise EvaluationApiError("request body must be a JSON object")
    agent = getattr(app, "agent", None)
    if not agent:
        raise EvaluationApiError("LLM agent is not enabled", status=503)

    prompt = extract_chat_prompt(payload)
    if not prompt:
        raise EvaluationApiError("messages must contain a non-empty user message")

    run_id = _request_run_id(payload)
    session_id = _request_session_id(payload, run_id)
    model = str(payload.get("model") or getattr(agent, "model", "") or "flood-emergency-agent")

    content_parts: list[str] = []
    for event in _agent_chat_stream(
        agent,
        prompt,
        session_id,
        run_id,
        allowed_tools=None,
        trace_user_message=prompt,
    ):
        content = _completion_text_from_event(event)
        if content:
            content_parts.append(content)

    content = "".join(content_parts).strip()
    if not content:
        content = "已接收请求，但本轮未生成面向用户的最终文本。请在交互式工作台继续确认或补充条件。"

    trace_id, span_id = _trace_context_for_run(agent, run_id)
    return _chat_completion_body(
        run_id=run_id,
        model=model,
        content=content,
        trace_id=trace_id,
    ), _trace_headers(trace_id, span_id)


def _chat_completion_body(*, run_id: str, model: str, content: str,
                          trace_id: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{run_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "trace_id": trace_id,
    }


def _trace_headers(trace_id: str, span_id: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if trace_id:
        headers["X-Trace-Id"] = trace_id
        if span_id:
            headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
    return headers


def extract_chat_prompt(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list):
        user_messages = [
            _message_text(message)
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        if user_messages:
            return user_messages[-1].strip()
        all_messages = [_message_text(message) for message in messages if isinstance(message, dict)]
        return "\n".join(item for item in all_messages if item).strip()

    input_value = payload.get("input")
    if isinstance(input_value, str):
        return input_value.strip()
    if isinstance(input_value, list):
        return "\n".join(_message_text(item) for item in input_value if isinstance(item, dict)).strip()
    return ""


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                value = part.get("text", part.get("content"))
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return ""


def _request_run_id(payload: dict[str, Any]) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    configured = metadata.get("run_id") or payload.get("run_id")
    if isinstance(configured, str) and configured.strip():
        return _safe_identifier(configured.strip())
    return f"eval-{uuid.uuid4().hex}"


def _request_session_id(payload: dict[str, Any], run_id: str) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    configured = metadata.get("session_id") or payload.get("user")
    if isinstance(configured, str) and configured.strip():
        return _safe_identifier(configured.strip())
    return f"chatcmpl:{run_id}"


def _safe_identifier(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", ":"} else "-" for ch in value)[:96]


def _agent_chat_stream(
    agent: Any,
    prompt: str,
    session_id: str,
    run_id: str,
    *,
    allowed_tools: Iterable[str] | None,
    trace_user_message: str,
) -> Iterable[Any]:
    kwargs = {
        "session_id": session_id,
        "allowed_tools": allowed_tools,
    }
    try:
        parameters = set(inspect.signature(agent.chat_stream).parameters)
        if "run_id" in parameters:
            kwargs["run_id"] = run_id
        if "trace_user_message" in parameters:
            kwargs["trace_user_message"] = trace_user_message
    except (TypeError, ValueError):
        pass
    return agent.chat_stream(prompt, **kwargs)


def _completion_text_from_event(event: Any) -> str:
    if isinstance(event, TextEvent):
        return event.content or ""
    if isinstance(event, QuestionEvent):
        options = "、".join(str(item.get("label") or item.get("value") or item) for item in event.options)
        suffix = f" 可选项：{options}" if options else ""
        return f"\n需要补充信息：{event.question}{suffix}\n"
    if isinstance(event, ConfirmationEvent):
        return (
            f"\n工具 {event.tool_name} 需要人工确认后才能执行。"
            "当前 HTTP 接口不会代替用户确认，请在交互式工作台中继续处理。\n"
        )
    if isinstance(event, HookBlockedEvent):
        return f"\n操作被安全策略阻止：{event.reason}\n"
    if isinstance(event, ToolResultEvent) and event.blocked:
        return f"\n工具 {event.name} 未能执行：{event.result}\n"
    return ""


def _trace_context_for_run(agent: Any, run_id: str) -> tuple[str, str]:
    recorder = getattr(getattr(agent, "harness", None), "genai_trace", None)
    snapshot = getattr(recorder, "snapshot", None)
    if not callable(snapshot):
        return "", ""
    for span in reversed(snapshot()):
        attributes = dict(getattr(span, "attributes", None) or {})
        if attributes.get("oag.run.id") != run_id:
            continue
        context = span.get_span_context()
        if not getattr(context, "is_valid", False):
            return "", ""
        return f"{context.trace_id:032x}", f"{context.span_id:016x}"
    return "", ""
