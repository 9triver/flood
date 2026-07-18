from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any


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


class AgentRunManager:
    def __init__(self, app: FloodApp):
        self.app = app
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
        yield self._format_sse("run", {
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
                yield self._format_sse("ping", {"type": "ping"})
                continue
            for event in pending:
                next_seq = int(event["seq"]) + 1
                yield self._format_sse(event["type"], event["data"])
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
            self.app.stream_chat(run)
        finally:
            self.app._append_event(run, "done", {"type": "done"})
            with run.condition:
                run.done = True
                run.updated_at = time.time()
                run.condition.notify_all()
            with self._lock:
                if self._active_by_session.get(run.session_id) == run.run_id:
                    self._active_by_session.pop(run.session_id, None)

    @staticmethod
    def _format_sse(event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

