"""Persistence: the journal is the only durable truth.

A JSONL sink appends every record to disk (flushed); ``load_journal``
replays a file back into a live Journal.  ``Kernel.recover`` then rebuilds
world state (namespace), capabilities and pending transactions from the
replayed journal — fsck on boot.  Derived views, mounts and processes are
*code* and are re-registered by the domain before recover() runs.
"""

from __future__ import annotations

import itertools
import json
from typing import Optional

from .capabilities import Capability
from .devices import PendingTxn
from .journal import Journal, Record


class JsonlSink:
    """Journal sink: one JSON object per line, flushed on every append."""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8")

    def __call__(self, record: Record) -> None:
        self._fh.write(json.dumps(record.as_dict(), ensure_ascii=False, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def load_journal(path: str) -> Journal:
    journal = Journal()
    last_seq = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            record = Record(seq=int(data["seq"]), ts=float(data["ts"]), kind=data["kind"], payload=data["payload"])
            journal._records.append(record)
            last_seq = record.seq
    journal._seq = itertools.count(last_seq + 1)
    return journal


def recover(kernel) -> dict:
    """Rebuild kernel state from its journal (boot-time fsck).

    Call after mounts / grants-by-code / derives / spawns are re-registered.
    Pending transactions that were awaiting approval or dispatched get a
    *fresh* grace window (restart must not instantly expire them); frozen
    paths survive restart until explicitly thawed.
    """
    stats = {"observations": 0, "txns": 0, "capabilities": 0, "thawed": 0}
    for record in kernel.journal.replay():
        kind, payload = record.kind, record.payload
        if kind == "observation":
            kernel.namespace.write(payload["path"], payload["value"], source_seq=record.seq, ts=record.ts)
            stats["observations"] += 1
        elif kind == "capability" and payload.get("event") == "grant":
            kernel.caps.register(
                Capability(
                    token=payload["token"],
                    prefix=payload["prefix"],
                    actions=frozenset(payload["actions"]),
                    granted_by=payload["granted_by"],
                    description=payload.get("description", ""),
                )
            )
            stats["capabilities"] += 1
        elif kind == "txn":
            _recover_txn(kernel, record, payload, stats)
        elif kind == "note" and payload.get("event") == "thaw":
            kernel.consistency.restore_unfreeze(payload["path"])
            stats["thawed"] += 1
    # fresh grace windows for still-open transactions
    now = kernel.clock()
    for txn in kernel.consistency.pending():
        driver = kernel.drivers.get(txn.device_id)
        if txn.state == "awaiting_approval":
            txn.approval_deadline = now + kernel.default_approval_timeout
        elif txn.state == "dispatched" and driver is not None:
            deadline = driver.default_txn_timeout
            txn.deadline = now + deadline if deadline else None
    return stats


def _recover_txn(kernel, record: Record, payload: dict, stats: dict) -> None:
    event = payload.get("event")
    txn_id = payload.get("txn_id")
    if event == "open":
        kernel.consistency.restore_open(
            PendingTxn(
                txn_id=txn_id,
                device_id=payload.get("device_id", ""),
                path=payload["path"],
                action=payload["action"],
                args=payload.get("args") or {},
                expect=payload.get("expect"),
                opened_seq=record.seq,
            )
        )
        stats["txns"] += 1
        return
    txn = kernel.consistency.find(txn_id)
    if txn is None:
        return
    if event == "dispatch":
        txn.dispatched_seq = record.seq
    elif event in ("awaiting_approval", "dispatched", "committed", "failed", "unknown"):
        kernel.consistency.restore_state(txn, event, payload.get("error"))
