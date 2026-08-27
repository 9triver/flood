"""The observation mirror: a queryable digital twin of recent history.

The namespace holds the *present* (latest value per path); the journal
holds *all* immutable facts; the mirror is the access path in between —
per-path series indexed by **world time** (``observed_at``), with a
retention policy, kept hot for O(1)-ish appends and cheap queries.

It is deliberately a mirror, not a database:

- only raw observations are stored (no aggregation — windows are
  application logic, computed against mirror queries);
- queries are by path and world-time range only;
- it is not authoritative: it can be rebuilt at any time from the
  journal (see recover), and retention may drop old samples that remain
  in the durable journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600.0
DEFAULT_MAX_PER_PATH = 100_000


@dataclass(frozen=True)
class Sample:
    path: str
    observed_at: float
    source_seq: int
    value: object

    def as_dict(self) -> dict:
        return {"path": self.path, "observed_at": self.observed_at, "source_seq": self.source_seq, "value": self.value}


class ObservationHistory:
    """Per-path rings of (observed_at, source_seq, value), lazily swept."""

    def __init__(
        self,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        max_per_path: int = DEFAULT_MAX_PER_PATH,
    ):
        self.retention_seconds = float(retention_seconds)
        self.max_per_path = int(max_per_path)
        self._series: dict[str, list[tuple[float, int, object]]] = {}

    # ---------------------------------------------------------------- write

    def append(self, path: str, value: object, observed_at: float, source_seq: int) -> None:
        series = self._series.get(path)
        if series is None:
            series = self._series[path] = []
        series.append((float(observed_at), int(source_seq), value))
        # lazy sweep: bound memory before correctness (retention trims below)
        if len(series) > self.max_per_path:
            del series[: len(series) - self.max_per_path]
        self._sweep(series)

    def _sweep(self, series: list) -> None:
        if not series or self.retention_seconds <= 0:
            return
        horizon = series[-1][0] - self.retention_seconds
        if series[0][0] < horizon:
            cut = 0
            for index, (observed_at, _seq, _value) in enumerate(series):
                if observed_at >= horizon:
                    cut = index
                    break
            else:
                cut = len(series)
            if cut:
                del series[:cut]

    # ----------------------------------------------------------------- read

    def query(
        self,
        path: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[Sample]:
        """Samples for one path ordered by observed_at ascending.  ``since``
        is inclusive, ``until`` exclusive; ``limit`` keeps the newest."""
        series = self._series.get(path, ())
        selected = [
            (observed_at, seq, value)
            for observed_at, seq, value in series
            if (since is None or observed_at >= since) and (until is None or observed_at < until)
        ]
        if limit is not None and len(selected) > limit:
            selected = selected[-limit:]
        return [Sample(path=path, observed_at=o, source_seq=s, value=v) for o, s, v in selected]

    def paths(self, under: str = "/") -> list[str]:
        under = under.rstrip("/")
        return sorted(p for p in self._series if under == "" or p == under or p.startswith(under + "/"))

    def last_observed_at(self, path: str) -> Optional[float]:
        series = self._series.get(path)
        return series[-1][0] if series else None

    def stats(self) -> dict:
        return {"paths": len(self._series), "samples": sum(len(s) for s in self._series.values())}
