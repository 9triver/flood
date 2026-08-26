"""Read-only query and event-subscription boundary for Domain OS clients."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from .models import (
    Command,
    CommandState,
    DerivedProduct,
    DomainEvent,
    ProjectedValue,
    Resource,
)


class DomainQueryError(RuntimeError):
    pass


class DomainRecordNotFound(DomainQueryError):
    pass


class DomainReadModel(Protocol):
    """Minimal live read model required by the external query boundary."""

    domain_id: str

    def resources(self, *, resource_type: str | None = None) -> tuple[Resource, ...]: ...

    def projection(self, resource_id: str) -> Mapping[str, ProjectedValue]: ...

    def products(
        self,
        *,
        product_type: str | None = None,
        subject_id: str | None = None,
    ) -> tuple[DerivedProduct, ...]: ...

    def events(self, *, event_type: str | None = None) -> tuple[DomainEvent, ...]: ...

    def commands(self) -> tuple[Command, ...]: ...

    def subscribe(
        self,
        handler: Callable[[DomainEvent], Any],
        *,
        event_type: str | None = None,
    ) -> Callable[[], None]: ...


class DomainQueryService:
    """Expose a stable JSON read model without depending on an Agent runtime."""

    def __init__(self, source: DomainReadModel) -> None:
        self.source = source
        self._condition = threading.Condition()
        self._event_version = 0
        self._dispose = source.subscribe(self._on_event)

    @property
    def domain_id(self) -> str:
        return self.source.domain_id

    def close(self) -> None:
        self._dispose()

    def projections(
        self,
        *,
        resource_id: str | None = None,
        resource_type: str | None = None,
    ) -> dict[str, Any]:
        selected_id = _optional_text(resource_id)
        selected_type = _optional_text(resource_type)
        resources = self.source.resources(resource_type=selected_type)
        if selected_id is not None:
            resources = tuple(
                resource for resource in resources
                if resource.resource_id == selected_id
            )
            if not resources:
                raise DomainRecordNotFound(f"unknown resource: {selected_id}")

        items = []
        for resource in resources:
            values = self.source.projection(resource.resource_id)
            if not values and selected_id is None:
                continue
            items.append({
                "resource": resource_to_dict(resource),
                "values": {
                    metric: projected_value_to_dict(value)
                    for metric, value in sorted(values.items())
                },
            })
        return {
            "domain_id": self.domain_id,
            "items": items,
            "count": len(items),
        }

    def products(
        self,
        *,
        product_type: str | None = None,
        subject_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        selected_offset, selected_limit = _window(offset, limit)
        values = self.source.products(
            product_type=_optional_text(product_type),
            subject_id=_optional_text(subject_id),
        )
        page = values[selected_offset:selected_offset + selected_limit]
        return {
            "domain_id": self.domain_id,
            "items": [product_to_dict(product) for product in page],
            "count": len(page),
            "total": len(values),
            "offset": selected_offset,
            "limit": selected_limit,
        }

    def product(self, product_id: str) -> dict[str, Any]:
        return product_to_dict(self.product_record(product_id))

    def product_record(self, product_id: str) -> DerivedProduct:
        selected_id = _required_text(product_id, "product_id")
        for product in self.source.products():
            if product.product_id == selected_id:
                return product
        raise DomainRecordNotFound(f"unknown product: {selected_id}")

    def event(self, event_id: str) -> dict[str, Any]:
        selected_id = _required_text(event_id, "event_id")
        for event in self.source.events():
            if event.event_id == selected_id:
                return event_to_dict(event)
        raise DomainRecordNotFound(f"unknown event: {selected_id}")

    def commands(
        self,
        *,
        state: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        capability_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        selected_offset, selected_limit = _window(offset, limit)
        selected_state = _optional_text(state)
        if (
            selected_state is not None
            and selected_state not in {value.value for value in CommandState}
        ):
            raise ValueError(f"unknown command state: {selected_state}")
        selected_resource = _optional_text(resource_id)
        selected_actor = _optional_text(actor_id)
        selected_capability = _optional_text(capability_id)
        values = self.source.commands()
        if selected_state is not None:
            values = tuple(
                command for command in values
                if command.state.value == selected_state
            )
        if selected_resource is not None:
            values = tuple(
                command for command in values
                if command.intent.resource_id == selected_resource
            )
        if selected_actor is not None:
            values = tuple(
                command for command in values
                if command.intent.actor_id == selected_actor
            )
        if selected_capability is not None:
            values = tuple(
                command for command in values
                if command.intent.capability_id == selected_capability
            )
        page = values[selected_offset:selected_offset + selected_limit]
        return {
            "domain_id": self.domain_id,
            "items": [command_to_dict(command) for command in page],
            "count": len(page),
            "total": len(values),
            "offset": selected_offset,
            "limit": selected_limit,
        }

    def command(self, command_id: str) -> dict[str, Any]:
        selected_id = _required_text(command_id, "command_id")
        for command in self.source.commands():
            if command.command_id == selected_id:
                return command_to_dict(command)
        raise DomainRecordNotFound(f"unknown command: {selected_id}")

    def events(
        self,
        *,
        after: int = 0,
        event_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        selected_after = _nonnegative_integer(after, "after")
        selected_limit = _positive_limit(limit)
        selected_type = _optional_text(event_type)
        selected_subject = _optional_text(subject_id)
        timeline = self.source.events()
        items = []
        last_scanned_cursor = selected_after
        for cursor, event in enumerate(timeline, start=1):
            if cursor <= selected_after:
                continue
            last_scanned_cursor = cursor
            if selected_type is not None and event.event_type != selected_type:
                continue
            if selected_subject is not None and event.subject_id != selected_subject:
                continue
            items.append({
                "cursor": cursor,
                "event": event_to_dict(event),
            })
            if len(items) == selected_limit:
                break

        next_cursor = (
            items[-1]["cursor"]
            if items
            else max(selected_after, last_scanned_cursor)
        )
        return {
            "domain_id": self.domain_id,
            "items": items,
            "count": len(items),
            "after": selected_after,
            "next_cursor": next_cursor,
            "head_cursor": len(timeline),
            "limit": selected_limit,
        }

    def wait_for_events(
        self,
        *,
        after: int = 0,
        event_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        wait_seconds = float(timeout)
        if wait_seconds < 0:
            raise ValueError("timeout must not be negative")
        with self._condition:
            event_version = self._event_version
        result = self.events(
            after=after,
            event_type=event_type,
            subject_id=subject_id,
            limit=limit,
        )
        if result["items"] or wait_seconds == 0:
            return result
        with self._condition:
            if self._event_version == event_version:
                self._condition.wait(timeout=wait_seconds)
        return self.events(
            after=result["next_cursor"],
            event_type=event_type,
            subject_id=subject_id,
            limit=limit,
        )

    def _on_event(self, event: DomainEvent) -> None:
        del event
        with self._condition:
            self._event_version += 1
            self._condition.notify_all()


def resource_to_dict(resource: Resource) -> dict[str, Any]:
    return {
        "resource_id": resource.resource_id,
        "resource_type": resource.resource_type,
        "name": resource.name,
        "driver_id": resource.driver_id,
        "capabilities": sorted(resource.capabilities),
        "attributes": dict(resource.attributes),
    }


def projected_value_to_dict(value: ProjectedValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "unit": value.unit,
        "observed_at": value.observed_at.isoformat(),
        "received_at": value.received_at.isoformat(),
        "quality": value.quality.value,
        "observation_id": value.observation_id,
    }


def product_to_dict(product: DerivedProduct) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "product_type": product.product_type,
        "subject_id": product.subject_id,
        "producer_id": product.producer_id,
        "generated_at": product.generated_at.isoformat(),
        "valid_from": product.valid_from.isoformat() if product.valid_from else None,
        "valid_to": product.valid_to.isoformat() if product.valid_to else None,
        "input_refs": list(product.input_refs),
        "data": dict(product.data),
        "artifacts": dict(product.artifacts),
        "correlation_id": product.correlation_id,
        "causation_id": product.causation_id,
    }


def event_to_dict(event: DomainEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "subject_id": event.subject_id,
        "occurred_at": event.occurred_at.isoformat(),
        "data": dict(event.data),
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
    }


def intent_to_dict(intent: Any) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "actor_id": intent.actor_id,
        "resource_id": intent.resource_id,
        "capability_id": intent.capability_id,
        "arguments": dict(intent.arguments),
        "requested_at": intent.requested_at.isoformat(),
        "rationale": intent.rationale,
        "correlation_id": intent.correlation_id,
    }


def command_to_dict(command: Command) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "intent": intent_to_dict(command.intent),
        "driver_id": command.driver_id,
        "state": command.state.value,
        "created_at": command.created_at.isoformat(),
        "updated_at": command.updated_at.isoformat(),
        "policy_reason": command.policy_reason,
        "approved_by": command.approved_by,
        "rejected_by": command.rejected_by,
        "rejection_reason": command.rejection_reason,
        "dispatched_at": (
            command.dispatched_at.isoformat()
            if command.dispatched_at is not None
            else None
        ),
        "external_id": command.external_id,
        "expected_state": dict(command.expected_state),
        "output": dict(command.output),
        "error": command.error,
    }


def _window(offset: Any, limit: Any) -> tuple[int, int]:
    return _nonnegative_integer(offset, "offset"), _positive_limit(limit)


def _positive_limit(value: Any) -> int:
    result = _nonnegative_integer(value, "limit")
    if result < 1 or result > 500:
        raise ValueError("limit must be between 1 and 500")
    return result


def _nonnegative_integer(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{label} must not be negative")
    return result


def _required_text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label} is required")
    return result


def _optional_text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None
