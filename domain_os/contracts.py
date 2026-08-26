"""Ports between a domain operating system and external infrastructure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from .models import (
    Capability,
    Command,
    CommandResult,
    DerivedProduct,
    DomainEvent,
    DriverHealth,
    Intent,
    Observation,
    PolicyDecision,
    Resource,
)


ObservationSink = Callable[[Observation], Awaitable[None]]


@runtime_checkable
class InfrastructureDriver(Protocol):
    """Connects domain resources to observation and command transports."""

    driver_id: str

    async def start(self, sink: ObservationSink) -> None: ...

    async def stop(self) -> None: ...

    async def execute(self, command: Command) -> CommandResult: ...

    def health(self) -> DriverHealth: ...


@runtime_checkable
class DomainPolicy(Protocol):
    """Authorizes an actor's intent without depending on an agent runtime."""

    def evaluate(
        self,
        intent: Intent,
        resource: Resource,
        capability: Capability,
    ) -> PolicyDecision: ...


@runtime_checkable
class DomainStore(Protocol):
    """Persists facts and control records independently of runtime processes."""

    def load_observations(self, domain_id: str) -> tuple[Observation, ...]: ...

    def load_commands(self, domain_id: str) -> tuple[Command, ...]: ...

    def load_events(self, domain_id: str) -> tuple[DomainEvent, ...]: ...

    def load_products(self, domain_id: str) -> tuple[DerivedProduct, ...]: ...

    def append_observation(
        self,
        domain_id: str,
        observation: Observation,
    ) -> None: ...

    def save_command(self, domain_id: str, command: Command) -> None: ...

    def append_event(self, domain_id: str, event: DomainEvent) -> None: ...

    def append_product(
        self,
        domain_id: str,
        product: DerivedProduct,
    ) -> None: ...
