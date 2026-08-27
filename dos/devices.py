"""Device drivers: the only code that touches real infrastructure.

A driver plays three roles:

- *top half*   — ``interrupt`` delivery: raw uplink bytes/messages enter the
  kernel and are queued; nothing trusts them yet.
- *bottom half* — ``normalize`` turns one raw interrupt into (path, value)
  commits; the kernel journals them before committing to the namespace.
- *downlink*   — ``dispatch`` sends a command to the device for an open
  transaction; ``privileged_actions`` names actions that require human
  approval (privilege escalation) before dispatch.
- *fsck rule*  — ``verify`` inspects the namespace to decide whether a
  pending transaction is confirmed, refuted, or still pending.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class PendingTxn:
    txn_id: str
    device_id: str
    path: str
    action: str
    args: dict
    expect: Optional[dict]  # precondition on namespace state (path -> value)
    state: str = "open"  # open | awaiting_approval | dispatched | committed | failed | unknown
    opened_seq: int = 0
    dispatched_seq: int = 0
    deadline: Optional[float] = None
    approval_deadline: Optional[float] = None
    approval: Optional[dict] = None
    error: Optional[str] = None
    outcome_seq: Optional[int] = None

    @property
    def terminal(self) -> bool:
        return self.state in ("committed", "failed", "unknown")


class Driver(ABC):
    device_id: str = ""
    kernel = None  # set by attach()

    # privileged actions require approval (privilege escalation) before dispatch
    privileged_actions: frozenset[str] = frozenset()
    default_txn_timeout: Optional[float] = 30.0

    def attach(self, kernel) -> None:  # kernel: dos.kernel.Kernel (loose import to avoid cycle)
        self.kernel = kernel

    # ------------------------------------------------------------ top half

    @abstractmethod
    def normalize(self, raw: object) -> Iterable[tuple[str, object]]:
        """Map one raw interrupt to (namespace path, value) commits."""

    # -------------------------------------------------------------- downlink

    @abstractmethod
    def dispatch(self, txn: PendingTxn) -> None:
        """Send the command to the device."""

    # ----------------------------------------------------------- fsck rule

    @abstractmethod
    def verify(self, txn: PendingTxn, read) -> str:
        """Return "committed" | "failed" | "pending" given current namespace.

        ``read`` is a callable path -> Snapshot-or-None.
        """

    # ------------------------------------------------------------ utilities

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        """Reject invalid act() arguments *before* a transaction is opened
        (and before approval).  Return an error string, or None if valid."""
        return None

    def on_timeout(self, txn: PendingTxn) -> str:
        """What fsck concludes when the deadline lapses without evidence."""
        return "unknown"
