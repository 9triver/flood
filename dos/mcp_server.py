"""MCP server exposing the dos syscall surface.

Tools map 1:1 to gateway syscalls — this is the syscall ABI's wire form
for agent runtimes (Python OAG, TS pi agent, anything with an MCP client):

    open_session(principal, read_scopes, act_prefix, act_actions)
    read_path(session, path)                -> value + generation
    list_paths(session, under)
    history(session, path, since, until, limit) -> mirror query by world time
    act(session, path, action, args, expect) -> txn handle (state may be
                                                awaiting_approval)
    pending_approvals()                      -> human-plane work list
    approve(txn_id, approved_by, decision, reason)
    txn_status(txn_id)
    wait_for_change(session, paths, since, timeout)  -> long-poll watch

Run ``scripts/dos_mcp_server.py`` for the flood demo instance (stdio).
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from .gateway import DosGateway, GatewayError


def _safe(fn: Callable) -> Callable:
    """Return kernel/gateway errors as structured payloads instead of opaque
    tool exceptions — agents (models) read the error text.  Uses the
    dedicated ``gateway_error`` key so domain fields named "error" (e.g.
    txn.error) never collide."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (GatewayError, PermissionError, KeyError, ValueError, RuntimeError) as exc:
            return {"gateway_error": f"{type(exc).__name__}: {exc}"}

    return wrapper


def build_mcp_server(gateway: DosGateway, *, name: str = "dos", instructions: Optional[str] = None):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name=name,
        instructions=instructions
        or (
            "Domain OS syscall surface. Sessions carry read scopes and (if "
            "granted) an act capability; the capability token never leaves "
            "the kernel. Mutations open transactions confirmed later by "
            "telemetry — poll txn_status or wait_for_change."
        ),
    )

    @server.tool()
    @_safe
    def open_session(
        principal: str,
        read_scopes: Optional[list[str]] = None,
        act_prefix: Optional[str] = None,
        act_actions: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Open an agent session. read_scopes are path prefixes the session
        may read; act_prefix/act_actions request an act capability rooted at
        that subtree. Returns an opaque session id."""
        session = gateway.open_session(
            principal,
            read_scopes=read_scopes or ("/",),
            act_prefix=act_prefix,
            act_actions=act_actions or (),
        )
        return {"session_id": session.session_id, "principal": session.principal, "read_scopes": list(session.read_scopes), "has_act": session.has_act}

    @server.tool()
    @_safe
    def read_path(session: str, path: str) -> dict[str, Any]:
        """Read one namespace path: value plus its generation (staleness is
        detectable)."""
        return gateway.read(session, path)

    @server.tool()
    @_safe
    def list_paths(session: str, under: str = "/") -> list[str]:
        """List readable namespace paths under a prefix."""
        return gateway.list_paths(session, under)

    @server.tool()
    @_safe
    def history(
        session: str,
        path: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Query the observation mirror: raw samples of one path ordered by
        world time (observed_at, unix seconds).  Aggregation is the
        caller's job — the kernel mirrors reality, it does not interpret it."""
        return gateway.history(session, path, since, until, limit)

    @server.tool()
    @_safe
    def act(
        session: str,
        path: str,
        action: str,
        args: Optional[dict[str, Any]] = None,
        expect: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """The only mutating syscall. Privileged actions return
        awaiting_approval until approve() is called. Retrying an identical
        in-flight request reuses the same transaction."""
        return gateway.act(session, path, action, args, expect)

    @server.tool()
    @_safe
    def pending_approvals() -> list[dict[str, Any]]:
        """Transactions parked for human approval (the approval work list)."""
        return gateway.pending_approvals()

    @server.tool()
    @_safe
    def approve(txn_id: str, approved_by: str, decision: bool, reason: str = "") -> dict[str, Any]:
        """Resolve a parked privileged transaction (pkexec)."""
        return gateway.approve(txn_id, approved_by, decision, reason)

    @server.tool()
    @_safe
    def txn_status(txn_id: str) -> dict[str, Any]:
        """State of a transaction: open / awaiting_approval / dispatched /
        committed / failed / unknown."""
        return gateway.txn_status(txn_id)

    @server.tool()
    @_safe
    def wait_for_change(
        session: str,
        paths: list[str],
        since: Optional[dict[str, int]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Long-poll watch: block until a watched path's generation exceeds
        `since` (path -> generation), then return current generations."""
        return gateway.wait_for_change(session, paths, since or {}, timeout)

    return server
