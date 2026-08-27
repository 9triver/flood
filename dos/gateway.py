"""Agent-facing syscall gateway: sessions, read scoping, watch bridging.

This module is transport-free (threads only) and unit-testable without
the mcp SDK; ``dos/mcp_server.py`` puts the MCP wire on top.  It is where
agent-plane governance lives:

- **Sessions** — an agent runtime opens a session by *principal* name and
  receives an opaque ``session_id``.  The capability token minted for the
  session never leaves this process; agents hold only the session id.
- **Read scoping** — every ``read``/``list``/``wait_for_change`` is checked
  against the session's declared read scopes (path prefixes).  This is the
  read-side governance the kernel deliberately does not do.
- **Watch bridging** — ``wait_for_change`` is a long-poll over namespace
  generations, giving subscription semantics to request/response clients.

Bootstrap posture: ``open_session`` trusts the transport for identity.
Real deployments front this with authenticated transport (streamable HTTP
with OAuth headers) — see docs/domain-os/内核设计.md.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from .kernel import ActResult, Kernel


class GatewayError(RuntimeError):
    pass


class ReadScopeError(GatewayError, PermissionError):
    pass


@dataclass(frozen=True)
class GatewaySession:
    session_id: str
    principal: str
    read_scopes: tuple[str, ...]
    has_act: bool


@dataclass
class _SessionEntry:
    session: GatewaySession
    token: str
    last_used: float


def _in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    for scope in scopes:
        scope = scope.rstrip("/")
        if not scope:
            return True  # "/" or "" grants the whole world
        if path == scope or path.startswith(scope + "/"):
            return True
    return False


class DosGateway:
    def __init__(self, kernel: Kernel, *, allow_act_grant: bool = True, idle_ttl: Optional[float] = None):
        self.kernel = kernel
        self.allow_act_grant = allow_act_grant
        self.idle_ttl = idle_ttl  # standing sessions stay alive by being used
        self._sessions: dict[str, _SessionEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- sessions

    def open_session(
        self,
        principal: str,
        *,
        read_scopes=("/",),
        act_prefix: Optional[str] = None,
        act_actions=(),
    ) -> GatewaySession:
        if not str(principal or "").strip():
            raise GatewayError("principal is required")
        token = ""
        has_act = bool(act_prefix and act_actions)
        if has_act:
            if not self.allow_act_grant:
                raise GatewayError("this gateway does not mint act capabilities")
            token = self.kernel.grant(act_prefix, act_actions, f"gateway:{principal}").token
        session = GatewaySession(
            session_id="sess_" + uuid.uuid4().hex[:12],
            principal=principal,
            read_scopes=tuple(read_scopes) or (),
            has_act=has_act,
        )
        with self._lock:
            self._sessions[session.session_id] = _SessionEntry(session, token, time.time())
        return session

    def close_session(self, session_id: str) -> None:
        with self._lock:
            entry = self._sessions.pop(session_id, None)
        if entry is not None and entry.token:
            self.kernel.caps.revoke(entry.token)

    def session(self, session_id: str) -> GatewaySession:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise GatewayError(f"unknown session: {session_id}")
        entry.last_used = time.time()  # liveness: any syscall counts as activity
        return entry.session

    def activity(self) -> list[dict]:
        """Liveness snapshot of all open sessions (does not touch them)."""
        now = time.time()
        with self._lock:
            entries = list(self._sessions.values())
        return [
            {
                "session_id": e.session.session_id,
                "principal": e.session.principal,
                "has_act": e.session.has_act,
                "idle_seconds": round(now - e.last_used, 1),
            }
            for e in entries
        ]

    def reap_idle(self) -> list[str]:
        """Close sessions idle beyond idle_ttl (revoking their capabilities).
        Returns the closed session ids.  A no-op when idle_ttl is unset."""
        if self.idle_ttl is None:
            return []
        now = time.time()
        closed: list[str] = []
        with self._lock:
            for sid in [s for s, e in self._sessions.items() if now - e.last_used > self.idle_ttl]:
                entry = self._sessions.pop(sid)
                closed.append(sid)
                if entry.token:
                    self.kernel.caps.revoke(entry.token)
        return closed

    def _token_for(self, session_id: str) -> str:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise GatewayError(f"unknown session: {session_id}")
        entry.last_used = time.time()
        if entry.session.has_act and not entry.token:
            raise GatewayError("session capability was revoked")
        return entry.token

    # -------------------------------------------------------------- syscalls

    def read(self, session_id: str, path: str) -> dict:
        self._check_read_scope(session_id, path)
        snap = self.kernel.read(path)
        return {
            "path": snap.path,
            "value": snap.value,
            "generation": snap.generation,
            "ts": snap.ts,
            "source_seq": snap.source_seq,
            "derived": snap.derived,
        }

    def try_read(self, session_id: str, path: str) -> Optional[dict]:
        self._check_read_scope(session_id, path)
        snap = self.kernel.try_read(path)
        if snap is None:
            return None
        return {"path": snap.path, "value": snap.value, "generation": snap.generation, "ts": snap.ts, "source_seq": snap.source_seq, "derived": snap.derived}

    def list_paths(self, session_id: str, under: str = "/") -> list[str]:
        """List namespace paths under a prefix, filtered to the session's
        read scopes.  ``under`` itself need not be in scope — a session may
        always ask what it can see."""
        session = self.session(session_id)
        return [p for p in self.kernel.namespace.paths(under) if _in_scope(p, session.read_scopes)]

    def history(
        self,
        session_id: str,
        path: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Query the observation mirror: raw samples by world time."""
        self._check_read_scope(session_id, path)
        return [
            {"path": s.path, "observed_at": s.observed_at, "source_seq": s.source_seq, "value": s.value}
            for s in self.kernel.history(path, since, until, limit)
        ]

    def act(self, session_id: str, path: str, action: str, args: Optional[dict] = None, expect: Optional[dict] = None) -> dict:
        token = self._token_for(session_id)
        if not token:
            raise ReadScopeError("session holds no act capability")
        result: ActResult = self.kernel.act(token, path, action, args or {}, expect)
        return {"txn_id": result.txn_id, "state": result.state, "reused": result.reused}

    def approve(self, txn_id: str, approved_by: str, decision: bool, reason: str = "") -> dict:
        result = self.kernel.approve(txn_id, approved_by, decision, reason)
        return {"txn_id": result.txn_id, "state": result.state}

    def txn_status(self, txn_id: str) -> dict:
        txn = self.kernel.txn(txn_id)
        return {
            "txn_id": txn.txn_id,
            "state": txn.state,
            "path": txn.path,
            "action": txn.action,
            "args": txn.args,
            "error": txn.error,
            "approval": txn.approval,
        }

    def pending_approvals(self) -> list[dict]:
        out = []
        for txn in self.kernel.consistency.pending():
            if txn.state != "awaiting_approval":
                continue
            out.append({"txn_id": txn.txn_id, "path": txn.path, "action": txn.action, "args": txn.args, "approval_deadline": txn.approval_deadline})
        return out

    # -------------------------------------------------------- watch bridging

    def wait_for_change(self, session_id: str, paths, since: dict, timeout: float = 10.0) -> dict:
        """Long-poll: block until any watched path's generation exceeds the
        caller's ``since`` map (path -> generation), or timeout.  Returns the
        current generations of all watched paths either way."""
        watched = list(dict.fromkeys(paths))
        for path in watched:
            self._check_read_scope(session_id, path)
        since = {p.rstrip("/"): int(g) for p, g in (since or {}).items()}

        event = threading.Event()

        def on_commit(snapshot) -> None:
            event.set()

        cancels = [self.kernel.namespace.watch(path, on_commit) for path in watched]

        def current() -> dict:
            generations = {}
            for path in watched:
                snap = self.kernel.namespace.try_read(path)
                generations[path] = snap.generation if snap is not None else -1
            return generations

        try:
            deadline = time.time() + timeout
            while True:
                generations = current()
                changed = {p: g for p, g in generations.items() if g > since.get(p.rstrip("/"), -1)}
                if changed or time.time() >= deadline:
                    return {"changed": changed, "generations": generations}
                event.wait(max(0.01, deadline - time.time()))
                event.clear()
        finally:
            for cancel in cancels:
                cancel()

    # -------------------------------------------------------------- helpers

    def _check_read_scope(self, session_id: str, path: str) -> None:
        session = self.session(session_id)
        if not _in_scope(path, session.read_scopes):
            raise ReadScopeError(f"session {session.principal!r} may not read {path} (scopes: {list(session.read_scopes)})")
