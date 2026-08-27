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
    """Monotonic, append-only log.  Records are frozen and never edited.

    ``hot_tail`` bounds the in-memory window (the durable sink, if any,
    still receives every record; recovery reads the file).  The mirror
    (dos.history) takes over recent-history queries, so the hot journal
    only needs to serve recent audits."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        sink: Optional[Callable[[Record], None]] = None,
        hot_tail: Optional[int] = None,
    ):
        self._clock = clock
        self._sink = sink
        self._hot_tail = hot_tail
        self._records: list[Record] = []
        self._seq = itertools.count(1)

    def append(self, kind: str, payload: dict) -> Record:
        record = Record(seq=next(self._seq), ts=self._clock(), kind=kind, payload=dict(payload))
        self._records.append(record)
        if self._hot_tail is not None and len(self._records) > self._hot_tail:
            del self._records[: len(self._records) - self._hot_tail]
        if self._sink is not None:
            self._sink(record)
        return record

    def trim(self, keep: Optional[int] = None) -> None:
        keep = self._hot_tail if keep is None else keep
        if keep is not None and len(self._records) > keep:
            del self._records[: len(self._records) - keep]

    def attach_sink(self, sink: Callable[[Record], None]) -> None:
        """Persist future records (e.g. re-attaching a file sink after
        loading a journal back from disk)."""
        self._sink = sink

    def tail(self, after_seq: int = 0) -> list[Record]:
        return [r for r in self._records if r.seq > after_seq]

    def replay(self) -> Iterable[Record]:
        return list(self._records)

    @property
    def last_seq(self) -> int:
        return self._records[-1].seq if self._records else 0

    def __len__(self) -> int:
        return len(self._records)
