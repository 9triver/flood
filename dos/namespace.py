"""The mounted world: a hierarchical namespace with watches and derived views.

Design (file-system semantics):

- Raw state is committed at paths like ``/hydro/shanhu/stations/808J1510/level``.
- Every commit bumps the path's *generation*; readers always receive the
  generation together with the value so staleness is detectable.
- ``watch`` is inotify: a subtree subscription fired on every commit.
- ``derive`` registers a page-cache-like view: a value computed by a pure
  function over other paths.  It is a cache — when any dependency advances,
  the view is invalidated and lazily recomputed on next read.  Views are
  never authoritative.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Callable, Optional


class NotFound(KeyError):
    pass


@dataclass(frozen=True)
class Snapshot:
    path: str
    value: object
    generation: int
    ts: float
    source_seq: int
    derived: bool = False


@dataclass
class _Node:
    value: object = None
    generation: int = 0
    ts: float = 0.0
    source_seq: int = 0
    valid: bool = True
    is_derived: bool = False
    fn: Optional[Callable[["Namespace"], object]] = None
    depends_on: tuple[str, ...] = ()

    def snapshot(self, path: str) -> Snapshot:
        return Snapshot(
            path=path,
            value=self.value,
            generation=self.generation,
            ts=self.ts,
            source_seq=self.source_seq,
            derived=self.is_derived,
        )


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts)


def _matches(subscription: str, path: str) -> bool:
    """Subtree match with optional trailing glob, e.g. ``/a/*/level``."""
    if subscription.endswith("*"):
        return fnmatch.fnmatchcase(path, subscription)
    return path == subscription or path.startswith(subscription.rstrip("/") + "/")


class Namespace:
    def __init__(self):
        self._nodes: dict[str, _Node] = {}
        self._watchers: list[tuple[str, Callable[[Snapshot], None]]] = []
        self._invalidations: list[str] = []
        self.watch_errors: list[str] = []

    # ---------------------------------------------------------------- state

    def write(self, path: str, value: object, source_seq: int, ts: float = 0.0) -> bool:
        """Commit a raw value.  Returns True when the generation advanced."""
        path = normalize_path(path)
        node = self._nodes.get(path)
        advanced = node is None or node.value != value
        if node is None:
            node = _Node()
            self._nodes[path] = node
        if advanced:
            node.generation += 1
        node.value = value
        node.ts = ts
        node.source_seq = source_seq
        self._invalidate_dependents(path)
        snapshot = node.snapshot(path)
        for sub, cb in list(self._watchers):
            if _matches(sub, path):
                try:
                    cb(snapshot)
                except Exception as exc:  # watchers must never corrupt the commit path
                    self.watch_errors.append(f"{path}: {type(exc).__name__}: {exc}")
        return advanced

    def read(self, path: str) -> Snapshot:
        path = normalize_path(path)
        node = self._nodes.get(path)
        if node is None:
            raise NotFound(path)
        if node.is_derived and not node.valid:
            self._recompute(path, node)
        return node.snapshot(path)

    def try_read(self, path: str) -> Optional[Snapshot]:
        try:
            return self.read(path)
        except NotFound:
            return None

    def exists(self, path: str) -> bool:
        return normalize_path(path) in self._nodes

    def paths(self, under: str = "/") -> list[str]:
        under = normalize_path(under)
        if under == "/":
            return sorted(self._nodes)
        prefix = under.rstrip("/") + "/"
        return sorted(p for p in self._nodes if p.startswith(prefix))

    # --------------------------------------------------------------- watches

    def watch(self, subscription: str, callback: Callable[[Snapshot], None]) -> Callable[[], None]:
        self._watchers.append((subscription, callback))

        def cancel() -> None:
            self._watchers = [(s, cb) for s, cb in self._watchers if not (s == subscription and cb is callback)]

        return cancel

    # ----------------------------------------------------------- derived views

    def derive(self, path: str, depends_on: tuple[str, ...], fn: Callable[["Namespace"], object]) -> None:
        """Register a derived view (page cache).  ``fn`` reads the namespace
        and must be pure; it may read other derived views."""
        path = normalize_path(path)
        node = _Node(is_derived=True, fn=fn, depends_on=tuple(normalize_path(d) for d in depends_on), valid=False)
        self._nodes[path] = node

    def invalidate(self, path: str) -> None:
        path = normalize_path(path)
        node = self._nodes.get(path)
        if node is not None and node.is_derived:
            node.valid = False
            self._invalidations.append(path)

    def _invalidate_dependents(self, changed: str) -> None:
        for p, node in self._nodes.items():
            if node.is_derived:
                for dep in node.depends_on:
                    if _matches(dep, changed):
                        node.valid = False
                        break

    def _recompute(self, path: str, node: _Node) -> None:
        # depth guard is unnecessary for well-formed (non-cyclic) registrations
        node.value = node.fn(self)
        node.valid = True
        node.generation += 1

    # ------------------------------------------------------------ diagnostics

    def pending_invalidations(self) -> list[str]:
        return list(self._invalidations)
