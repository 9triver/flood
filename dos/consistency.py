"""fsck / watchdog over pending transactions.

"Sending a command is not changing reality."  Every ``act()`` opens a
transaction.  Evidence for its outcome arrives later, as telemetry
interrupts that change the namespace.  The consistency checker re-verifies
pending transactions whenever a path they care about changes, and the
watchdog marks deadline-lapsed transactions *unknown* — and freezes the
affected path so nobody blindly re-issues an operation whose effect on the
real world is uncertain (re-opening a gate twice is not idempotent).
"""

from __future__ import annotations

from typing import Callable, Optional

from .devices import PendingTxn


class ConsistencyError(RuntimeError):
    pass


class Consistency:
    def __init__(self, journal, clock: Callable[[], float]):
        self._journal = journal
        self._clock = clock
        self._pending: dict[str, PendingTxn] = {}
        self._frozen: dict[str, str] = {}  # path -> reason
        self._on_transition: list[Callable[[PendingTxn], None]] = []

    # ---------------------------------------------------------------- state

    def open(self, txn: PendingTxn) -> None:
        self._pending[txn.txn_id] = txn
        self._journal.append("txn", {"txn_id": txn.txn_id, "event": "open", "path": txn.path, "action": txn.action, "args": txn.args})

    def get(self, txn_id: str) -> PendingTxn:
        return self._pending[txn_id]

    def pending(self) -> list[PendingTxn]:
        return [t for t in self._pending.values() if not t.terminal]

    def frozen_paths(self) -> dict[str, str]:
        return dict(self._frozen)

    def on_transition(self, cb: Callable[[PendingTxn], None]) -> None:
        self._on_transition.append(cb)

    # ------------------------------------------------------------ transitions

    def set_state(self, txn: PendingTxn, state: str, error: Optional[str] = None) -> None:
        if txn.terminal:
            raise ConsistencyError(f"txn {txn.txn_id} already terminal ({txn.state})")
        prev = txn.state
        txn.state = state
        txn.error = error
        txn.outcome_seq = self._journal.last_seq + 1
        record = self._journal.append("txn", {"txn_id": txn.txn_id, "event": state, "prev": prev, "error": error})
        txn.outcome_seq = record.seq
        if state == "unknown":
            self._frozen[txn.path] = f"txn {txn.txn_id} outcome unknown"
        for cb in self._on_transition:
            cb(txn)

    def thaw(self, path: str, reason: str) -> None:
        """Explicitly release a frozen path once evidence resolves the doubt."""
        self._frozen.pop(normalize(path), None)
        self._journal.append("note", {"event": "thaw", "path": path, "reason": reason})

    def is_frozen(self, path: str) -> Optional[str]:
        return self._frozen.get(normalize(path))

    # ----------------------------------------------------------------- fsck

    def check(self, read, drivers: dict) -> None:
        """Re-verify all dispatched-but-unresolved transactions."""
        for txn in self.pending():
            if txn.state != "dispatched":
                continue
            driver = drivers[txn.device_id]
            verdict = driver.verify(txn, read)
            if verdict == "committed":
                self.set_state(txn, "committed")
            elif verdict == "failed":
                self.set_state(txn, "failed", error="refuted by telemetry")

    def expire(self, drivers: dict) -> None:
        now = self._clock()
        for txn in self.pending():
            if txn.state != "dispatched" or txn.deadline is None:
                continue
            if now >= txn.deadline:
                verdict = drivers[txn.device_id].on_timeout(txn)
                self.set_state(txn, verdict, error="deadline lapsed without confirming evidence" if verdict == "unknown" else None)


def normalize(path: str) -> str:
    return "/" + "/".join(p for p in path.split("/") if p)
