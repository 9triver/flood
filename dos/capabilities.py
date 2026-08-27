"""Capabilities: unforgeable tokens are the only currency of authority.

Agents hold capabilities, not global rights.  A capability grants a set of
actions on a subtree of the namespace.  Tokens are minted by the kernel
(and by approval), never guessed: they are random ids known only to the
kernel's registry.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


class CapabilityError(PermissionError):
    pass


@dataclass(frozen=True)
class Capability:
    token: str
    prefix: str
    actions: frozenset[str]
    granted_by: str
    description: str = ""

    def permits(self, path: str, action: str) -> bool:
        if action not in self.actions:
            return False
        prefix = self.prefix.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")


class CapabilityRegistry:
    def __init__(self):
        self._by_token: dict[str, Capability] = {}

    def grant(self, prefix: str, actions, granted_by: str, description: str = "") -> Capability:
        actions = frozenset(actions)
        cap = Capability(
            token="cap_" + secrets.token_hex(8),
            prefix=prefix.rstrip("/"),
            actions=actions,
            granted_by=granted_by,
            description=description,
        )
        self._by_token[cap.token] = cap
        return cap

    def register(self, cap: Capability) -> Capability:
        """Re-register an existing capability verbatim (journal replay)."""
        self._by_token[cap.token] = cap
        return cap

    def revoke(self, token: str) -> None:
        self._by_token.pop(token, None)

    def check(self, token: str, path: str, action: str) -> Capability:
        cap = self._by_token.get(token)
        if cap is None:
            raise CapabilityError(f"unknown capability token for {path}:{action}")
        if not cap.permits(path, action):
            raise CapabilityError(
                f"capability granted by {cap.granted_by} does not permit {action} on {path} "
                f"(allows {sorted(cap.actions)} under {cap.prefix})"
            )
        return cap
