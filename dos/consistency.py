"""fsck / watchdog over pending transactions.

"Sending a command is not changing reality."  Every ``act()`` opens a
transaction.  Evidence for its outcome arrives later, as telemetry
interrupts that change the namespace.  The consistency checker re-verifies
pending transactions whenever the kernel pumps, and the watchdog marks
deadline-lapsed transactions *unknown* — and freezes the affected path so
nobody blindly re-issues an operation whose effect on the real world is
uncertain (re-opening a gate twice is not idempotent).

Evidence freshness: the reader handed to ``Driver.verify`` only shows
snapshots whose journal source_seq is *newer than the dispatch*.  Stale
state — including "the world already looked like the target before we
sent the command" — can never commit a transaction.
"""

from __future__ import annotations

from typing import Callable, Optional

from .devices import PendingTxn
from .journal import Record


class ConsistencyError(RuntimeError):
    pass


def _normalize(path: str) -> str:
    return "/" + "/".join(p for p in path.split("/") if p)


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
        self._journal.append(
            "txn",
            {
                "event": "open",
                "txn_id": txn.txn_id,
                "device_id": txn.device_id,
                "path": txn.path,
                "action": txn.action,
                "args": txn.args,
                "expect": txn.expect,
            },
        )

    def get(self, txn_id: str) -> PendingTxn:
        return self._pending[txn_id]

    def find(self, txn_id: str) -> Optional[PendingTxn]:
        return self._pending.get(txn_id)

    def pending(self) -> list[PendingTxn]:
        return [t for t in self._pending.values() if not t.terminal]

    def find_pending(self, device_id: str, path: str, action: str, args: dict, expect: Optional[dict]) -> Optional[PendingTxn]:
        """Idempotency key: an identical in-flight transaction, if any."""
        for txn in self.pending():
            if (
                txn.device_id == device_id
                and txn.path == path
                and txn.action == action
                and txn.args == args
                and txn.expect == expect
            ):
                return txn
        return None

    def frozen_paths(self) -> dict[str, str]:
        return dict(self._frozen)

    def on_transition(self, cb: Callable[[PendingTxn], None]) -> None:
        self._on_transition.append(cb)

    # ------------------------------------------------------------ transitions

    def set_state(self, txn: PendingTxn, state: str, error: Optional[str] = None, extra: Optional[dict] = None) -> Record:
        if txn.terminal:
            raise ConsistencyError(f"txn {txn.txn_id} already terminal ({txn.state})")
        prev = txn.state
        txn.state = state
        txn.error = error
        payload = {"txn_id": txn.txn_id, "event": state, "prev": prev, "error": error}
        if extra:
            payload.update(extra)
        record = self._journal.append("txn", payload)
        txn.outcome_seq = record.seq
        if state == "unknown":
            self._frozen[_normalize(txn.path)] = f"txn {txn.txn_id} outcome unknown"
        for cb in self._on_transition:
            cb(txn)
        return record

    def thaw(self, path: str, reason: str) -> None:
        """Explicitly release a frozen path once evidence resolves the doubt."""
        self._frozen.pop(_normalize(path), None)
        self._journal.append("note", {"event": "thaw", "path": path, "reason": reason})

    def is_frozen(self, path: str) -> Optional[str]:
        return self._frozen.get(_normalize(path))

    # ----------------------------------------------------------------- fsck

    def check(self, read, drivers: dict) -> None:
        """Re-verify all dispatched-but-unresolved transactions.
        ``read`` is path -> Snapshot|None over the whole namespace; evidence
        older than the dispatch is filtered out before the driver sees it."""
        for txn in self.pending():
            if txn.state != "dispatched":
                continue
            driver = drivers[txn.device_id]

            def evidence(path: str, txn: PendingTxn = txn):
                snap = read(path)
                if snap is None or snap.source_seq <= txn.dispatched_seq:
                    return None
                return snap

            verdict = driver.verify(txn, evidence)
            if verdict == "committed":
                self.set_state(txn, "committed")
            elif verdict == "failed":
                self.set_state(txn, "failed", error="refuted by telemetry")

    def expire(self, drivers: dict) -> None:
        now = self._clock()
        for txn in self.pending():
            if txn.state == "awaiting_approval" and txn.approval_deadline is not None and now >= txn.approval_deadline:
                self.set_state(txn, "failed", error="approval timed out")
            elif txn.state == "dispatched" and txn.deadline is not None and now >= txn.deadline:
                verdict = drivers[txn.device_id].on_timeout(txn)
                self.set_state(
                    txn,
                    verdict,
                    error="deadline lapsed without confirming evidence" if verdict == "unknown" else None,
                )

    # -------------------------------------------------------------- recovery
    # restore_* mutate without journaling — used only when replaying a journal

    def restore_open(self, txn: PendingTxn) -> None:
        self._pending[txn.txn_id] = txn

    def restore_state(self, txn: PendingTxn, state: str, error: Optional[str] = None) -> None:
        txn.state = state
        txn.error = error
        if state == "unknown":
            self._frozen[_normalize(txn.path)] = f"txn {txn.txn_id} outcome unknown"

    def restore_unfreeze(self, path: str) -> None:
        self._frozen.pop(_normalize(path), None)
