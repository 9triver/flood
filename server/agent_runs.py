from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Protocol

from server.serialization import format_sse


class AgentRun:
    def __init__(self, run_id: str, session_id: str, message: str,
                 selected: dict | None = None):
        self.run_id = run_id
        self.session_id = session_id
        self.message = message
        self.selected = selected or {}
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.cancelled = False
        self.seq = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.condition = threading.Condition()

    def append_event(self, event_type: str, data: dict[str, Any]) -> None:
        with self.condition:
            self.seq += 1
            self.events.append({
                "seq": self.seq,
                "type": event_type,
                "data": {**data, "seq": self.seq, "run_id": self.run_id},
            })
            self.updated_at = time.time()
            self.condition.notify_all()

    def mark_done(self) -> None:
        with self.condition:
            self.done = True
            self.updated_at = time.time()
            self.condition.notify_all()


class ChatStreamer(Protocol):
    def stream_chat(self, run: AgentRun) -> None: ...


class AgentRunManager:
    def __init__(self, chat_streamer: ChatStreamer):
        self.chat_streamer = chat_streamer
        self._runs: dict[str, AgentRun] = {}
        self._active_by_session: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, session_id: str, message: str,
              selected: dict | None = None) -> AgentRun:
        run = AgentRun(uuid.uuid4().hex, session_id, message, selected)
        with self._lock:
            self._runs[run.run_id] = run
            self._active_by_session[session_id] = run.run_id
        thread = threading.Thread(target=self._execute, args=(run,), daemon=True)
        thread.start()
        return run

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active(self, session_id: str) -> AgentRun | None:
        with self._lock:
            run_id = self._active_by_session.get(session_id)
            run = self._runs.get(run_id) if run_id else None
        if not run:
            return None
        with run.condition:
            return None if run.done or run.cancelled else run

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if not run:
            return False
        with run.condition:
            run.cancelled = True
            run.condition.notify_all()
        return True

    def stream(self, run: AgentRun, since: int = 0):
        yield format_sse("run", {
            "type": "run",
            "run_id": run.run_id,
            "session_id": run.session_id,
            "done": run.done,
            "seq": run.seq,
        })

        next_seq = max(1, int(since or 0) + 1)
        while True:
            pending = []
            done = False
            should_ping = False
            with run.condition:
                while not run.done and not run.cancelled and run.seq < next_seq:
                    run.condition.wait(timeout=15)
                    if run.seq < next_seq:
                        should_ping = True
                        break
                pending = [event for event in run.events if int(event.get("seq", 0)) >= next_seq]
                done = run.done or run.cancelled
            if should_ping:
                yield format_sse("ping", {"type": "ping"})
                continue
            for event in pending:
                next_seq = int(event["seq"]) + 1
                yield format_sse(event["type"], event["data"])
            if done and not pending:
                break

    def active_info(self, session_id: str) -> dict:
        run = self.get_active(session_id)
        if not run:
            return {"run_id": None}
        with run.condition:
            return {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "seq": run.seq,
                "done": run.done,
                "cancelled": run.cancelled,
            }

    def _execute(self, run: AgentRun):
        try:
            self.chat_streamer.stream_chat(run)
        finally:
            run.append_event("done", {"type": "done"})
            run.mark_done()
            with self._lock:
                if self._active_by_session.get(run.session_id) == run.run_id:
                    self._active_by_session.pop(run.session_id, None)
