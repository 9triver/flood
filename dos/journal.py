"""Append-only journal of immutable facts — the kernel's WAL.

Everything the kernel learns or decides becomes a Record here before it
has any effect.  The journal is the only durable truth; the namespace is
a rebuildable cache over it.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class Record:
    seq: int
    ts: float
    kind: str  # observation | txn | approval | note
    payload: dict

    def as_dict(self) -> dict:
        return {"seq": self.seq, "ts": self.ts, "kind": self.kind, "payload": self.payload}


class Journal:
    """Monotonic, append-only log.  Records are frozen and never edited."""

    def __init__(self, clock: Callable[[], float] = time.time, sink: Optional[Callable[[Record], None]] = None):
        self._clock = clock
        self._sink = sink
        self._records: list[Record] = []
        self._seq = itertools.count(1)

    def append(self, kind: str, payload: dict) -> Record:
        record = Record(seq=next(self._seq), ts=self._clock(), kind=kind, payload=dict(payload))
        self._records.append(record)
        if self._sink is not None:
            self._sink(record)
        return record

    def tail(self, after_seq: int = 0) -> list[Record]:
        return [r for r in self._records if r.seq > after_seq]

    def replay(self) -> Iterable[Record]:
        return list(self._records)

    @property
    def last_seq(self) -> int:
        return self._records[-1].seq if self._records else 0

    def __len__(self) -> int:
        return len(self._records)
