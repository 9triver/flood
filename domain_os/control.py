"""Runtime-independent Intent and approval service."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from .models import Command, CommandState, Intent, new_id, utc_now
from .query import DomainRecordNotFound, command_to_dict
from .runtime import DomainRuntimeError


class DomainControlConflict(RuntimeError):
    pass


class DomainControlModel(Protocol):
    domain_id: str

    def commands(self) -> tuple[Command, ...]: ...

    def command(self, command_id: str) -> Command: ...

    async def submit_intent(self, intent: Intent) -> Command: ...

    async def approve(self, command_id: str, *, approver_id: str) -> Command: ...

    async def reject(
        self,
        command_id: str,
        *,
        rejector_id: str,
        reason: str,
    ) -> Command: ...


class DomainControlService:
    """Validate transport data and invoke the governed runtime state machine."""

    def __init__(self, target: DomainControlModel) -> None:
        self.target = target

    async def submit_intent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = _payload(payload, {
            "intent_id",
            "actor_id",
            "resource_id",
            "capability_id",
            "arguments",
            "requested_at",
            "rationale",
            "correlation_id",
        })
        intent_id = _optional_text(request.get("intent_id")) or new_id("intent")
        existing = next(
            (
                command for command in self.target.commands()
                if command.intent.intent_id == intent_id
            ),
            None,
        )
        if existing is not None:
            if not _matches_request(existing.intent, request):
                raise DomainControlConflict(
                    f"intent id already exists with different content: {intent_id}"
                )
            return command_to_dict(existing)

        arguments = request.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise ValueError("arguments must be an object")
        requested_at = _optional_datetime(request.get("requested_at")) or utc_now()
        intent = Intent(
            intent_id=intent_id,
            actor_id=_required_text(request.get("actor_id"), "actor_id"),
            resource_id=_required_text(request.get("resource_id"), "resource_id"),
            capability_id=_required_text(
                request.get("capability_id"),
                "capability_id",
            ),
            arguments=dict(arguments),
            requested_at=requested_at,
            rationale=str(request.get("rationale") or "").strip(),
            correlation_id=_optional_text(request.get("correlation_id")),
        )
        return command_to_dict(await self.target.submit_intent(intent))

    async def approve(
        self,
        command_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = _payload(payload, {"approver_id"})
        command = self._pending_command(command_id)
        return command_to_dict(await self.target.approve(
            command.command_id,
            approver_id=_required_text(
                request.get("approver_id"),
                "approver_id",
            ),
        ))

    async def reject(
        self,
        command_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = _payload(payload, {"rejector_id", "reason"})
        command = self._pending_command(command_id)
        return command_to_dict(await self.target.reject(
            command.command_id,
            rejector_id=_required_text(
                request.get("rejector_id"),
                "rejector_id",
            ),
            reason=_required_text(request.get("reason"), "reason"),
        ))

    def _pending_command(self, command_id: str) -> Command:
        selected_id = _required_text(command_id, "command_id")
        try:
            command = self.target.command(selected_id)
        except DomainRuntimeError as exc:
            raise DomainRecordNotFound(
                f"unknown command: {selected_id}"
            ) from exc
        if command.state is not CommandState.PENDING_APPROVAL:
            raise DomainControlConflict(
                f"command is not pending approval: {selected_id}"
            )
        return command


def _matches_request(intent: Intent, request: Mapping[str, Any]) -> bool:
    arguments = request.get("arguments", {})
    if not isinstance(arguments, Mapping):
        return False
    if request.get("requested_at") not in (None, ""):
        try:
            requested_at = _optional_datetime(request.get("requested_at"))
        except ValueError:
            return False
        if requested_at != intent.requested_at:
            return False
    return (
        _optional_text(request.get("actor_id")) == intent.actor_id
        and _optional_text(request.get("resource_id")) == intent.resource_id
        and _optional_text(request.get("capability_id")) == intent.capability_id
        and dict(arguments) == dict(intent.arguments)
        and str(request.get("rationale") or "").strip() == intent.rationale
        and _optional_text(request.get("correlation_id")) == intent.correlation_id
    )


def _payload(
    value: Mapping[str, Any],
    allowed_fields: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("request body must be an object")
    unknown = set(value) - allowed_fields
    if unknown:
        raise ValueError(
            "unknown request fields: " + ", ".join(sorted(map(str, unknown)))
        )
    return dict(value)


def _required_text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{label} is required")
    return result


def _optional_text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None


def _optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("requested_at must be an ISO 8601 string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("requested_at must be an ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        raise ValueError("requested_at must include a timezone")
    return result


__all__ = [
    "DomainControlConflict",
    "DomainControlModel",
    "DomainControlService",
]
