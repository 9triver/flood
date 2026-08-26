"""SQLite persistence for domain facts, commands, and events."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Any

from .models import (
    Command,
    CommandState,
    DomainEvent,
    Intent,
    Observation,
    ObservationQuality,
)


class DomainPersistenceError(RuntimeError):
    pass


class SqliteDomainStore:
    """Durable single-process store for the reference domain runtime."""

    def __init__(self, path: str | PathLike[str]) -> None:
        database = str(path)
        if database != ":memory:":
            database = str(Path(database).expanduser().resolve())
            Path(database).parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        self._connection = sqlite3.connect(database)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS domain_observations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (domain_id, observation_id)
            );

            CREATE TABLE IF NOT EXISTS domain_commands (
                domain_id TEXT NOT NULL,
                command_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (domain_id, command_id)
            );

            CREATE TABLE IF NOT EXISTS domain_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (domain_id, event_id)
            );

            CREATE INDEX IF NOT EXISTS idx_domain_observations_domain
                ON domain_observations (domain_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_domain_events_domain
                ON domain_events (domain_id, sequence);
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def load_observations(self, domain_id: str) -> tuple[Observation, ...]:
        rows = self._connection.execute(
            """
            SELECT payload
              FROM domain_observations
             WHERE domain_id = ?
             ORDER BY sequence
            """,
            (domain_id,),
        ).fetchall()
        return tuple(_observation_from_dict(_json_load(row[0])) for row in rows)

    def load_commands(self, domain_id: str) -> tuple[Command, ...]:
        rows = self._connection.execute(
            """
            SELECT payload
              FROM domain_commands
             WHERE domain_id = ?
             ORDER BY created_at, command_id
            """,
            (domain_id,),
        ).fetchall()
        return tuple(_command_from_dict(_json_load(row[0])) for row in rows)

    def load_events(self, domain_id: str) -> tuple[DomainEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT payload
              FROM domain_events
             WHERE domain_id = ?
             ORDER BY sequence
            """,
            (domain_id,),
        ).fetchall()
        return tuple(_event_from_dict(_json_load(row[0])) for row in rows)

    def append_observation(
        self,
        domain_id: str,
        observation: Observation,
    ) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO domain_observations (
                        domain_id,
                        observation_id,
                        payload
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        domain_id,
                        observation.observation_id,
                        _json_dump(_observation_to_dict(observation)),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DomainPersistenceError(
                f"duplicate persisted observation: {observation.observation_id}"
            ) from exc

    def save_command(self, domain_id: str, command: Command) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO domain_commands (
                    domain_id,
                    command_id,
                    created_at,
                    payload
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (domain_id, command_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    payload = excluded.payload
                """,
                (
                    domain_id,
                    command.command_id,
                    command.created_at.isoformat(),
                    _json_dump(_command_to_dict(command)),
                ),
            )

    def append_event(self, domain_id: str, event: DomainEvent) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO domain_events (domain_id, event_id, payload)
                    VALUES (?, ?, ?)
                    """,
                    (
                        domain_id,
                        event.event_id,
                        _json_dump(_event_to_dict(event)),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DomainPersistenceError(
                f"duplicate persisted event: {event.event_id}"
            ) from exc


def _json_dump(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise DomainPersistenceError(
            "domain records must contain JSON-compatible values"
        ) from exc


def _json_load(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DomainPersistenceError("invalid persisted domain record") from exc
    if not isinstance(decoded, dict):
        raise DomainPersistenceError("persisted domain record must be an object")
    return decoded


def _observation_to_dict(observation: Observation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "resource_id": observation.resource_id,
        "metric": observation.metric,
        "value": observation.value,
        "unit": observation.unit,
        "observed_at": observation.observed_at.isoformat(),
        "received_at": observation.received_at.isoformat(),
        "quality": observation.quality.value,
        "sequence": observation.sequence,
        "source_ref": observation.source_ref,
        "attributes": dict(observation.attributes),
    }


def _observation_from_dict(value: Mapping[str, Any]) -> Observation:
    return Observation(
        observation_id=value["observation_id"],
        resource_id=value["resource_id"],
        metric=value["metric"],
        value=value.get("value"),
        unit=value.get("unit"),
        observed_at=_datetime(value["observed_at"]),
        received_at=_datetime(value["received_at"]),
        quality=ObservationQuality(value["quality"]),
        sequence=value.get("sequence"),
        source_ref=value.get("source_ref"),
        attributes=value.get("attributes", {}),
    )


def _intent_to_dict(intent: Intent) -> dict[str, Any]:
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


def _intent_from_dict(value: Mapping[str, Any]) -> Intent:
    return Intent(
        intent_id=value["intent_id"],
        actor_id=value["actor_id"],
        resource_id=value["resource_id"],
        capability_id=value["capability_id"],
        arguments=value.get("arguments", {}),
        requested_at=_datetime(value["requested_at"]),
        rationale=value.get("rationale", ""),
        correlation_id=value.get("correlation_id"),
    )


def _command_to_dict(command: Command) -> dict[str, Any]:
    return {
        "command_id": command.command_id,
        "intent": _intent_to_dict(command.intent),
        "driver_id": command.driver_id,
        "state": command.state.value,
        "created_at": command.created_at.isoformat(),
        "updated_at": command.updated_at.isoformat(),
        "policy_reason": command.policy_reason,
        "approved_by": command.approved_by,
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


def _command_from_dict(value: Mapping[str, Any]) -> Command:
    dispatched_at = value.get("dispatched_at")
    return Command(
        command_id=value["command_id"],
        intent=_intent_from_dict(value["intent"]),
        driver_id=value["driver_id"],
        state=CommandState(value["state"]),
        created_at=_datetime(value["created_at"]),
        updated_at=_datetime(value["updated_at"]),
        policy_reason=value.get("policy_reason", ""),
        approved_by=value.get("approved_by"),
        dispatched_at=_datetime(dispatched_at) if dispatched_at is not None else None,
        external_id=value.get("external_id"),
        expected_state=value.get("expected_state", {}),
        output=value.get("output", {}),
        error=value.get("error"),
    )


def _event_to_dict(event: DomainEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "subject_id": event.subject_id,
        "occurred_at": event.occurred_at.isoformat(),
        "data": dict(event.data),
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
    }


def _event_from_dict(value: Mapping[str, Any]) -> DomainEvent:
    return DomainEvent(
        event_id=value["event_id"],
        event_type=value["event_type"],
        subject_id=value["subject_id"],
        occurred_at=_datetime(value["occurred_at"]),
        data=value.get("data", {}),
        correlation_id=value.get("correlation_id"),
        causation_id=value.get("causation_id"),
    )


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DomainPersistenceError("persisted timestamp must be a string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise DomainPersistenceError(
            f"invalid persisted timestamp: {value}"
        ) from exc
