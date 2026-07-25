from __future__ import annotations

import json
from typing import TYPE_CHECKING

from oag.agent import Agent
from oag.ontology.schema import Ontology
from oag.runtime.events import event_to_dict

from domains.flood.runtime.workspace import active_workspace_id
from server.chat.policy import build_agent_task_hint, select_user_agent_tools
from server.chat.side_effects import AgentSideEffects

if TYPE_CHECKING:
    from server.agent_runs import AgentRun


class FloodChatService:
    """Adapt the generic OAG Agent to frontend flood-chat runs."""

    def __init__(self, agent: Agent | None, ontology: Ontology,
                 side_effects: AgentSideEffects):
        self.agent = agent
        self.ontology = ontology
        self.side_effects = side_effects

    def stream_chat(self, run: AgentRun) -> None:
        selected = run.selected or {}
        if not self.agent:
            run.append_event("text", {
                "type": "text",
                "content": "当前未启用 LLM，无法由智能体推理并调用地图工具。",
            })
            return

        agent_session_id = self.agent_session_id(run.session_id)
        recent_user_context = "\n".join(
            item.get("content", "")
            for item in self.agent.get_history(agent_session_id)[-6:]
            if item.get("role") == "user"
        )
        allowed_tools = select_user_agent_tools(
            run.message, self.ontology, recent_user_context,
        )
        try:
            for event in self.agent.chat_stream(
                self._agent_message(run.message, selected),
                session_id=agent_session_id,
                allowed_tools=allowed_tools,
            ):
                if run.cancelled:
                    break
                self._append_pending_frontend_events(run, agent_session_id)
                data = event_to_dict(event)
                run.append_event(data["type"], data)
            self._append_pending_frontend_events(run, agent_session_id)
        except Exception as exc:
            print(f"OAG agent stream failed: {exc}")
            run.append_event("text", {
                "type": "text",
                "content": f"智能体生成失败：{exc}",
            })

    @staticmethod
    def agent_session_id(session_id: str) -> str:
        return f"{active_workspace_id() or 'manual'}:{session_id}"

    def _append_pending_frontend_events(self, run: AgentRun,
                                        session_id: str) -> None:
        for result in self.side_effects.pop_map_events(session_id):
            run.append_event("map_actions", {
                "type": "map_actions",
                "context": result.get("context"),
                "map_actions": result.get("map_actions", []),
                "result_cards": result.get("result_cards", []),
                "llm_enabled": bool(self.agent),
            })
        for result in self.side_effects.pop_directive_events(session_id):
            run.append_event("directive_draft", {
                "type": "directive_draft",
                "draft": result.get("draft", {}),
            })

    def _agent_message(self, message: str, selected: dict) -> str:
        task_hint = build_agent_task_hint(message, self.ontology)
        frontend_context = {
            "用户问题": message,
            "选中对象": selected,
        }
        return (
            f"用户问题：{message}\n\n"
            f"{task_hint}\n"
            "以下是前端 GIS 的当前状态，只用于帮助你理解用户正在看的"
            "地图。不要把这些前端动作当作领域事实；领域事实必须通过 "
            "OAG 工具查询。"
            "这里的地图状态只作为本轮动态上下文。\n"
            f"{json.dumps(frontend_context, ensure_ascii=False, indent=2)}"
        )
