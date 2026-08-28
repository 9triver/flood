from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from oag.runtime.events import ConfirmationEvent, TextEvent  # noqa: E402
from server.evaluation_api import (  # noqa: E402
    EvaluationApiError,
    build_chat_completion_response,
    extract_chat_prompt,
)


TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "89abcdef01234567"


class FakeSpan:
    def __init__(self, run_id: str):
        self.attributes = {"oag.run.id": run_id}

    def get_span_context(self):
        return SimpleNamespace(
            is_valid=True,
            trace_id=int(TRACE_ID, 16),
            span_id=int(SPAN_ID, 16),
        )


class FakeRecorder:
    def __init__(self):
        self.spans = []

    def snapshot(self):
        return list(self.spans)


class FakeAgent:
    def __init__(self):
        self.harness = SimpleNamespace(genai_trace=FakeRecorder())
        self.calls = []

    def chat_stream(self, message, session_id="default", allowed_tools=None, run_id="", trace_user_message=""):
        self.calls.append({
            "message": message,
            "session_id": session_id,
            "allowed_tools": allowed_tools,
            "run_id": run_id,
            "trace_user_message": trace_user_message,
        })
        yield TextEvent(content="收到：")
        yield TextEvent(content=message)
        self.harness.genai_trace.spans.append(FakeSpan(run_id))


class ConfirmationAgent(FakeAgent):
    def chat_stream(self, message, session_id="default", allowed_tools=None, run_id=""):
        self.calls.append({"message": message, "session_id": session_id, "run_id": run_id})
        yield ConfirmationEvent(tool_name="dispatch_directive", reason="需要人工确认")
        self.harness.genai_trace.spans.append(FakeSpan(run_id))


class EvaluationApiTest(unittest.TestCase):
    def test_extracts_last_user_message_from_chat_payload(self):
        self.assertEqual(
            "第二个问题",
            extract_chat_prompt({
                "messages": [
                    {"role": "system", "content": "系统提示"},
                    {"role": "user", "content": "第一个问题"},
                    {"role": "assistant", "content": "回答"},
                    {"role": "user", "content": "第二个问题"},
                ],
            }),
        )

    def test_openai_completion_response_contains_trace_id(self):
        agent = FakeAgent()
        app = SimpleNamespace(agent=agent)

        body, headers = build_chat_completion_response(app, {
            "model": "demo-model",
            "messages": [{"role": "user", "content": "判断淹没影响"}],
            "metadata": {"run_id": "eval-001", "session_id": "case-001"},
        })

        self.assertEqual("chat.completion", body["object"])
        self.assertEqual("收到：判断淹没影响", body["choices"][0]["message"]["content"])
        self.assertEqual(TRACE_ID, body["trace_id"])
        self.assertEqual(TRACE_ID, headers["X-Trace-Id"])
        self.assertEqual(f"00-{TRACE_ID}-{SPAN_ID}-01", headers["traceparent"])
        self.assertEqual("case-001", agent.calls[0]["session_id"])
        self.assertEqual("eval-001", agent.calls[0]["run_id"])
        self.assertEqual("判断淹没影响", agent.calls[0]["trace_user_message"])

    def test_default_session_id_is_generic_chat_completion_run(self):
        agent = FakeAgent()

        build_chat_completion_response(SimpleNamespace(agent=agent), {
            "messages": [{"role": "user", "content": "判断淹没影响"}],
            "run_id": "case-no-session",
        })

        self.assertEqual("chatcmpl:case-no-session", agent.calls[0]["session_id"])

    def test_dynamic_runner_metadata_does_not_change_agent_execution_path(self):
        agent = FakeAgent()
        original_prompt = "请为平竹村转移单置点的避洪转移路线，使用驾车模式，避开预测淹没禁行区域。"

        build_chat_completion_response(SimpleNamespace(agent=agent), {
            "messages": [{"role": "user", "content": original_prompt}],
            "metadata": {"source": "dynamic-evaluation-runner"},
            "run_id": "eval-c4-route",
        })

        self.assertIsNone(agent.calls[0]["allowed_tools"])
        self.assertEqual(original_prompt, agent.calls[0]["message"])
        self.assertEqual(original_prompt, agent.calls[0]["trace_user_message"])

    def test_confirmation_event_becomes_regular_assistant_text(self):
        body, _ = build_chat_completion_response(SimpleNamespace(agent=ConfirmationAgent()), {
            "messages": [{"role": "user", "content": "发送应急指令"}],
            "run_id": "eval-002",
        })

        self.assertIn("需要人工确认", body["choices"][0]["message"]["content"])
        self.assertEqual(TRACE_ID, body["trace_id"])

    def test_missing_agent_returns_service_error(self):
        with self.assertRaises(EvaluationApiError) as raised:
            build_chat_completion_response(SimpleNamespace(agent=None), {
                "messages": [{"role": "user", "content": "测试"}],
            })
        self.assertEqual(503, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
