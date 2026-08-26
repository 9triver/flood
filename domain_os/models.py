"""Core records of an agent-oriented domain operating system."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _timestamp(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("value must be a mapping")
    return MappingProxyType(dict(value))


class ObservationQuality(str, Enum):
    GOOD = "good"
    SUSPECT = "suspect"
    BAD = "bad"


class CapabilityRisk(str, Enum):
    LOW = "low"
    CONTROLLED = "controlled"
    CRITICAL = "critical"


class CommandState(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    DISPATCHING = "dispatching"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ACKNOWLEDGED = "acknowledged"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Capability:
    capability_id: str
    description: str
    risk: CapabilityRisk = CapabilityRisk.LOW
    idempotent: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capability_id",
            _required(self.capability_id, "capability_id"),
        )
        object.__setattr__(
            self,
            "description",
            _required(self.description, "capability description"),
        )


@dataclass(frozen=True, slots=True)
class Resource:
    resource_id: str
    resource_type: str
    name: str
    driver_id: str
    capabilities: frozenset[str] = frozenset()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_id", _required(self.resource_id, "resource_id"))
        object.__setattr__(
            self,
            "resource_type",
            _required(self.resource_type, "resource_type"),
        )
        object.__setattr__(self, "name", _required(self.name, "resource name"))
        object.__setattr__(self, "driver_id", _required(self.driver_id, "driver_id"))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "attributes", _mapping(self.attributes))


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    resource_id: str
    metric: str
    value: Any
    unit: str | None
    observed_at: datetime
    received_at: datetime
    quality: ObservationQuality = ObservationQuality.GOOD
    sequence: int | None = None
    source_ref: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_id",
            _required(self.observation_id, "observation_id"),
        )
        object.__setattr__(self, "resource_id", _required(self.resource_id, "resource_id"))
        object.__setattr__(self, "metric", _required(self.metric, "metric"))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "received_at", _timestamp(self.received_at, "received_at"))
        object.__setattr__(self, "attributes", _mapping(self.attributes))
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("observation sequence must not be negative")


@dataclass(frozen=True, slots=True)
class ProjectedValue:
    value: Any
    unit: str | None
    observed_at: datetime
    received_at: datetime
    quality: ObservationQuality
    observation_id: str


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: str
    event_type: str
    subject_id: str
    occurred_at: datetime
    data: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        object.__setattr__(self, "event_type", _required(self.event_type, "event_type"))
        object.__setattr__(self, "subject_id", _required(self.subject_id, "subject_id"))
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "data", _mapping(self.data))


@dataclass(frozen=True, slots=True)
class Intent:
    intent_id: str
    actor_id: str
    resource_id: str
    capability_id: str
    arguments: Mapping[str, Any]
    requested_at: datetime
    rationale: str = ""
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _required(self.intent_id, "intent_id"))
        object.__setattr__(self, "actor_id", _required(self.actor_id, "actor_id"))
        object.__setattr__(self, "resource_id", _required(self.resource_id, "resource_id"))
        object.__setattr__(
            self,
            "capability_id",
            _required(self.capability_id, "capability_id"),
        )
        object.__setattr__(self, "arguments", _mapping(self.arguments))
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.allowed and self.requires_approval:
            raise ValueError("a rejected policy decision cannot require approval")


@dataclass(frozen=True, slots=True)
class CommandResult:
    accepted: bool
    external_id: str | None = None
    output: Mapping[str, Any] = field(default_factory=dict)
    expected_state: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _mapping(self.output))
        object.__setattr__(self, "expected_state", _mapping(self.expected_state))
        if not self.accepted and not self.error:
            raise ValueError("a rejected command result must include an error")


@dataclass(frozen=True, slots=True)
class Command:
    command_id: str
    intent: Intent
    driver_id: str
    state: CommandState
    created_at: datetime
    updated_at: datetime
    policy_reason: str = ""
    approved_by: str | None = None
    dispatched_at: datetime | None = None
    external_id: str | None = None
    expected_state: Mapping[str, Any] = field(default_factory=dict)
    output: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _required(self.command_id, "command_id"))
        object.__setattr__(self, "driver_id", _required(self.driver_id, "driver_id"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        if self.dispatched_at is not None:
            object.__setattr__(
                self,
                "dispatched_at",
                _timestamp(self.dispatched_at, "dispatched_at"),
            )
        object.__setattr__(self, "expected_state", _mapping(self.expected_state))
        object.__setattr__(self, "output", _mapping(self.output))

    def transition(self, state: CommandState, **changes: Any) -> Command:
        return replace(self, state=state, updated_at=utc_now(), **changes)


@dataclass(frozen=True, slots=True)
class DriverHealth:
    driver_id: str
    connected: bool
    checked_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "driver_id", _required(self.driver_id, "driver_id"))
        object.__setattr__(self, "checked_at", _timestamp(self.checked_at, "checked_at"))
        object.__setattr__(self, "details", _mapping(self.details))
