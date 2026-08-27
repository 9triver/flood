from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


ACTIVE_OPERATION_STATES = frozenset({"awaiting_approval", "dispatched"})
TERMINAL_OPERATION_STATES = frozenset({"committed", "failed", "unknown"})


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_path(path: str) -> str:
    normalized = "/" + "/".join(part for part in str(path).split("/") if part)
    if normalized == "/":
        return normalized
    return normalized.rstrip("/")


def path_covers(prefix: str, path: str) -> bool:
    prefix = normalize_path(prefix)
    path = normalize_path(path)
    return prefix == "/" or path == prefix or path.startswith(prefix + "/")


@dataclass(frozen=True)
class Snapshot:
    path: str
    value: Any
    revision: int
    observed_at: float
    recorded_at: float
    source: str


@dataclass(frozen=True)
class NormalizedObservation:
    path: str
    value: Any
    observed_at: float
    source: str


@dataclass(frozen=True)
class Observation:
    seq: int
    path: str
    value: Any
    observed_at: float
    recorded_at: float
    source: str


@dataclass(frozen=True)
class JournalRecord:
    seq: int
    recorded_at: float
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Capability:
    capability_id: str
    token: str
    principal: str
    prefix: str
    actions: frozenset[str]
    expires_at: float | None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    device_id: str
    resource_path: str
    action: str
    arguments: dict[str, Any]
    capability_id: str
    expected_revision: int | None
    state: str
    opened_seq: int
    dispatched_seq: int | None
    deadline: float | None
    error: str | None
    approval: dict[str, Any] | None
    created_at: float
    updated_at: float

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_OPERATION_STATES


@dataclass(frozen=True)
class ActResult:
    operation_id: str
    state: str
    reused: bool = False


@dataclass(frozen=True)
class Verification:
    state: str
    error: str | None = None

    @classmethod
    def pending(cls) -> "Verification":
        return cls("pending")

    @classmethod
    def committed(cls) -> "Verification":
        return cls("committed")

    @classmethod
    def failed(cls, error: str) -> "Verification":
        return cls("failed", error)
