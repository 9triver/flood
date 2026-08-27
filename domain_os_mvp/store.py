from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import CapabilityError, InvalidStateError, NotFoundError
from .model import (
    ACTIVE_OPERATION_STATES,
    Capability,
    JournalRecord,
    Observation,
    Operation,
    Snapshot,
    canonical_json,
    normalize_path,
    path_covers,
)


class SQLiteStore:
    """Durable fact ledger with rebuildable current-state projections."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS world_state (
                    path TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    observed_at REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    source TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS capabilities (
                    capability_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    principal TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    expires_at REAL,
                    revoked_at REAL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    resource_path TEXT NOT NULL,
                    action TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    expected_revision INTEGER,
                    state TEXT NOT NULL,
                    opened_seq INTEGER NOT NULL,
                    dispatched_seq INTEGER,
                    deadline REAL,
                    error TEXT,
                    approval_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_operation_per_resource
                ON operations(resource_path)
                WHERE state IN ('awaiting_approval', 'dispatched');

                CREATE TABLE IF NOT EXISTS frozen_resources (
                    resource_path TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    frozen_at REAL NOT NULL
                );
                """
            )

    @contextmanager
    def _transaction(self):
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                yield cursor
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _append(
        cursor: sqlite3.Cursor,
        kind: str,
        payload: dict[str, Any],
        recorded_at: float,
    ) -> int:
        cursor.execute(
            "INSERT INTO journal(recorded_at, kind, payload_json) VALUES (?, ?, ?)",
            (recorded_at, kind, canonical_json(payload)),
        )
        return int(cursor.lastrowid)

    # -------------------------------------------------------------- journal

    def record_system_event(
        self,
        event: str,
        payload: dict[str, Any],
        recorded_at: float,
    ) -> int:
        with self._transaction() as cursor:
            return self._append(
                cursor,
                "system",
                {"event": event, **payload},
                recorded_at,
            )

    def journal(
        self,
        *,
        after_seq: int = 0,
        kind: str | None = None,
    ) -> list[JournalRecord]:
        sql = "SELECT * FROM journal WHERE seq > ?"
        parameters: list[Any] = [int(after_seq)]
        if kind is not None:
            sql += " AND kind = ?"
            parameters.append(kind)
        sql += " ORDER BY seq"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [
            JournalRecord(
                seq=int(row["seq"]),
                recorded_at=float(row["recorded_at"]),
                kind=str(row["kind"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    @property
    def journal_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM journal"
            ).fetchone()
        return int(row["count"])

    # ---------------------------------------------------------- observations

    def record_observation(
        self,
        path: str,
        value: Any,
        observed_at: float,
        source: str,
        recorded_at: float,
    ) -> tuple[Observation, bool]:
        path = normalize_path(path)
        payload = {
            "path": path,
            "value": value,
            "observed_at": float(observed_at),
            "source": str(source),
        }
        projected = False
        with self._transaction() as cursor:
            seq = self._append(cursor, "observation", payload, recorded_at)
            current = cursor.execute(
                "SELECT observed_at FROM world_state WHERE path = ?",
                (path,),
            ).fetchone()
            if current is None or float(observed_at) >= float(current["observed_at"]):
                cursor.execute(
                    """
                    INSERT INTO world_state(
                        path, value_json, revision, observed_at, recorded_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        value_json = excluded.value_json,
                        revision = excluded.revision,
                        observed_at = excluded.observed_at,
                        recorded_at = excluded.recorded_at,
                        source = excluded.source
                    """,
                    (
                        path,
                        canonical_json(value),
                        seq,
                        float(observed_at),
                        float(recorded_at),
                        str(source),
                    ),
                )
                projected = True
        return (
            Observation(
                seq=seq,
                path=path,
                value=value,
                observed_at=float(observed_at),
                recorded_at=float(recorded_at),
                source=str(source),
            ),
            projected,
        )

    def read(self, path: str) -> Snapshot | None:
        path = normalize_path(path)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM world_state WHERE path = ?",
                (path,),
            ).fetchone()
        if row is None:
            return None
        return Snapshot(
            path=str(row["path"]),
            value=json.loads(row["value_json"]),
            revision=int(row["revision"]),
            observed_at=float(row["observed_at"]),
            recorded_at=float(row["recorded_at"]),
            source=str(row["source"]),
        )

    def history(
        self,
        path: str,
        *,
        since: float | None = None,
        after_seq: int = 0,
    ) -> list[Observation]:
        selected = normalize_path(path)
        observations = []
        for record in self.journal(after_seq=after_seq, kind="observation"):
            payload = record.payload
            if normalize_path(payload["path"]) != selected:
                continue
            observed_at = float(payload["observed_at"])
            if since is not None and observed_at < float(since):
                continue
            observations.append(
                Observation(
                    seq=record.seq,
                    path=selected,
                    value=payload["value"],
                    observed_at=observed_at,
                    recorded_at=record.recorded_at,
                    source=str(payload["source"]),
                )
            )
        return observations

    def observations_after(self, seq: int) -> list[Observation]:
        observations = []
        for record in self.journal(after_seq=seq, kind="observation"):
            payload = record.payload
            observations.append(
                Observation(
                    seq=record.seq,
                    path=normalize_path(payload["path"]),
                    value=payload["value"],
                    observed_at=float(payload["observed_at"]),
                    recorded_at=record.recorded_at,
                    source=str(payload["source"]),
                )
            )
        return observations

    def rebuild_projections(self) -> None:
        """Atomically rebuild mutable tables from the append-only journal.

        The kernel must not pump while this maintenance operation is running.
        """
        with self._transaction() as cursor:
            records = cursor.execute(
                "SELECT * FROM journal ORDER BY seq"
            ).fetchall()
            cursor.execute("DELETE FROM frozen_resources")
            cursor.execute("DELETE FROM operations")
            cursor.execute("DELETE FROM capabilities")
            cursor.execute("DELETE FROM world_state")

            for record in records:
                seq = int(record["seq"])
                recorded_at = float(record["recorded_at"])
                kind = str(record["kind"])
                payload = json.loads(record["payload_json"])

                if kind == "observation":
                    path = normalize_path(payload["path"])
                    current = cursor.execute(
                        "SELECT observed_at FROM world_state WHERE path = ?",
                        (path,),
                    ).fetchone()
                    observed_at = float(payload["observed_at"])
                    if current is not None and observed_at < float(current["observed_at"]):
                        continue
                    cursor.execute(
                        """
                        INSERT INTO world_state(
                            path, value_json, revision, observed_at,
                            recorded_at, source
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            value_json = excluded.value_json,
                            revision = excluded.revision,
                            observed_at = excluded.observed_at,
                            recorded_at = excluded.recorded_at,
                            source = excluded.source
                        """,
                        (
                            path,
                            canonical_json(payload["value"]),
                            seq,
                            observed_at,
                            recorded_at,
                            str(payload["source"]),
                        ),
                    )
                    continue

                if kind == "capability" and payload.get("event") == "granted":
                    cursor.execute(
                        """
                        INSERT INTO capabilities(
                            capability_id, token_hash, principal, prefix,
                            actions_json, expires_at, revoked_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                        """,
                        (
                            payload["capability_id"],
                            payload["token_hash"],
                            payload["principal"],
                            normalize_path(payload["prefix"]),
                            canonical_json(payload["actions"]),
                            payload.get("expires_at"),
                            recorded_at,
                        ),
                    )
                    continue

                if kind == "operation" and payload.get("event") == "opened":
                    cursor.execute(
                        """
                        INSERT INTO operations(
                            operation_id, device_id, resource_path, action,
                            arguments_json, capability_id, expected_revision,
                            state, opened_seq, dispatched_seq, deadline, error,
                            approval_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL, NULL,
                                  NULL, NULL, ?, ?)
                        """,
                        (
                            payload["operation_id"],
                            payload["device_id"],
                            normalize_path(payload["resource_path"]),
                            payload["action"],
                            canonical_json(payload.get("arguments") or {}),
                            payload["capability_id"],
                            payload.get("expected_revision"),
                            seq,
                            recorded_at,
                            recorded_at,
                        ),
                    )
                    continue

                if kind == "operation" and payload.get("event") in {
                    "awaiting_approval",
                    "dispatched",
                    "committed",
                    "failed",
                    "unknown",
                }:
                    state = str(payload["event"])
                    cursor.execute(
                        """
                        UPDATE operations
                        SET state = ?,
                            dispatched_seq = CASE
                                WHEN ? = 'dispatched' THEN ?
                                ELSE dispatched_seq
                            END,
                            deadline = CASE
                                WHEN ? = 'dispatched' THEN ?
                                ELSE deadline
                            END,
                            error = ?, updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (
                            state,
                            state,
                            seq,
                            state,
                            payload.get("deadline"),
                            payload.get("error"),
                            recorded_at,
                            payload["operation_id"],
                        ),
                    )
                    continue

                if kind == "approval":
                    approval = {
                        key: payload.get(key)
                        for key in (
                            "approved_by",
                            "decision",
                            "reason",
                            "decided_at",
                        )
                    }
                    cursor.execute(
                        """
                        UPDATE operations
                        SET approval_json = ?, updated_at = ?
                        WHERE operation_id = ?
                        """,
                        (
                            canonical_json(approval),
                            recorded_at,
                            payload["operation_id"],
                        ),
                    )
                    continue

                if kind == "system" and payload.get("event") == "resource_frozen":
                    cursor.execute(
                        """
                        INSERT INTO frozen_resources(
                            resource_path, operation_id, reason, frozen_at
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(resource_path) DO UPDATE SET
                            operation_id = excluded.operation_id,
                            reason = excluded.reason,
                            frozen_at = excluded.frozen_at
                        """,
                        (
                            normalize_path(payload["resource_path"]),
                            payload["operation_id"],
                            payload["reason"],
                            recorded_at,
                        ),
                    )

    # ---------------------------------------------------------- capabilities

    def grant_capability(
        self,
        principal: str,
        prefix: str,
        actions: Iterable[str],
        *,
        expires_at: float | None,
        now: float,
    ) -> Capability:
        capability_id = f"cap_{secrets.token_hex(8)}"
        token = f"{capability_id}.{secrets.token_urlsafe(24)}"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        normalized_prefix = normalize_path(prefix)
        normalized_actions = frozenset(str(action) for action in actions)
        if not normalized_actions:
            raise ValueError("a capability must grant at least one action")
        payload = {
            "event": "granted",
            "capability_id": capability_id,
            "token_hash": token_hash,
            "principal": str(principal),
            "prefix": normalized_prefix,
            "actions": sorted(normalized_actions),
            "expires_at": expires_at,
        }
        with self._transaction() as cursor:
            self._append(cursor, "capability", payload, now)
            cursor.execute(
                """
                INSERT INTO capabilities(
                    capability_id, token_hash, principal, prefix, actions_json,
                    expires_at, revoked_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    capability_id,
                    token_hash,
                    str(principal),
                    normalized_prefix,
                    canonical_json(sorted(normalized_actions)),
                    expires_at,
                    now,
                ),
            )
        return Capability(
            capability_id=capability_id,
            token=token,
            principal=str(principal),
            prefix=normalized_prefix,
            actions=normalized_actions,
            expires_at=expires_at,
        )

    def check_capability(
        self,
        token: str,
        path: str,
        action: str,
        *,
        now: float,
    ) -> Capability:
        token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM capabilities WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise CapabilityError("invalid or revoked capability")
        expires_at = row["expires_at"]
        if expires_at is not None and now >= float(expires_at):
            raise CapabilityError("capability has expired")
        prefix = str(row["prefix"])
        actions = frozenset(json.loads(row["actions_json"]))
        if not path_covers(prefix, path) or action not in actions:
            raise CapabilityError(
                f"capability does not allow {action} on {normalize_path(path)}"
            )
        return Capability(
            capability_id=str(row["capability_id"]),
            token=str(token),
            principal=str(row["principal"]),
            prefix=prefix,
            actions=actions,
            expires_at=float(expires_at) if expires_at is not None else None,
        )

    # ------------------------------------------------------------ operations

    def active_operation(self, resource_path: str) -> Operation | None:
        resource_path = normalize_path(resource_path)
        placeholders = ",".join("?" for _ in ACTIVE_OPERATION_STATES)
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT * FROM operations
                WHERE resource_path = ? AND state IN ({placeholders})
                ORDER BY opened_seq DESC LIMIT 1
                """,
                [resource_path, *sorted(ACTIVE_OPERATION_STATES)],
            ).fetchone()
        return self._operation_from_row(row) if row is not None else None

    def create_operation(
        self,
        *,
        operation_id: str,
        device_id: str,
        resource_path: str,
        action: str,
        arguments: dict[str, Any],
        capability_id: str,
        expected_revision: int | None,
        initial_state: str,
        deadline: float | None,
        now: float,
    ) -> Operation:
        resource_path = normalize_path(resource_path)
        open_payload = {
            "event": "opened",
            "operation_id": operation_id,
            "device_id": device_id,
            "resource_path": resource_path,
            "action": action,
            "arguments": arguments,
            "capability_id": capability_id,
            "expected_revision": expected_revision,
        }
        with self._transaction() as cursor:
            opened_seq = self._append(cursor, "operation", open_payload, now)
            cursor.execute(
                """
                INSERT INTO operations(
                    operation_id, device_id, resource_path, action,
                    arguments_json, capability_id, expected_revision, state,
                    opened_seq, dispatched_seq, deadline, error, approval_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    operation_id,
                    device_id,
                    resource_path,
                    action,
                    canonical_json(arguments),
                    capability_id,
                    expected_revision,
                    opened_seq,
                    now,
                    now,
                ),
            )
            state_seq = self._append(
                cursor,
                "operation",
                {
                    "event": initial_state,
                    "operation_id": operation_id,
                    "deadline": deadline,
                },
                now,
            )
            dispatched_seq = state_seq if initial_state == "dispatched" else None
            cursor.execute(
                """
                UPDATE operations
                SET state = ?, dispatched_seq = ?, deadline = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (initial_state, dispatched_seq, deadline, now, operation_id),
            )
        return self.operation(operation_id)

    def resolve_approval(
        self,
        operation_id: str,
        *,
        approved_by: str,
        decision: bool,
        reason: str,
        deadline: float | None,
        now: float,
    ) -> Operation:
        approval = {
            "approved_by": str(approved_by),
            "decision": bool(decision),
            "reason": str(reason),
            "decided_at": now,
        }
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT state FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"unknown operation: {operation_id}")
            if row["state"] != "awaiting_approval":
                raise InvalidStateError(
                    f"operation {operation_id} is not awaiting approval"
                )
            self._append(
                cursor,
                "approval",
                {"operation_id": operation_id, **approval},
                now,
            )
            state = "dispatched" if decision else "failed"
            error = None if decision else "rejected by approver"
            state_seq = self._append(
                cursor,
                "operation",
                {
                    "event": state,
                    "operation_id": operation_id,
                    "deadline": deadline if decision else None,
                    "error": error,
                },
                now,
            )
            cursor.execute(
                """
                UPDATE operations
                SET state = ?, dispatched_seq = ?, deadline = ?, error = ?,
                    approval_json = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    state,
                    state_seq if decision else None,
                    deadline if decision else None,
                    error,
                    canonical_json(approval),
                    now,
                    operation_id,
                ),
            )
        return self.operation(operation_id)

    def transition_operation(
        self,
        operation_id: str,
        state: str,
        *,
        error: str | None,
        now: float,
        freeze: bool = False,
    ) -> Operation:
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT resource_path FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"unknown operation: {operation_id}")
            resource_path = str(row["resource_path"])
            self._append(
                cursor,
                "operation",
                {
                    "event": state,
                    "operation_id": operation_id,
                    "error": error,
                },
                now,
            )
            cursor.execute(
                """
                UPDATE operations
                SET state = ?, error = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (state, error, now, operation_id),
            )
            if freeze:
                reason = error or f"operation {operation_id} outcome is unknown"
                cursor.execute(
                    """
                    INSERT INTO frozen_resources(
                        resource_path, operation_id, reason, frozen_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(resource_path) DO UPDATE SET
                        operation_id = excluded.operation_id,
                        reason = excluded.reason,
                        frozen_at = excluded.frozen_at
                    """,
                    (resource_path, operation_id, reason, now),
                )
                self._append(
                    cursor,
                    "system",
                    {
                        "event": "resource_frozen",
                        "resource_path": resource_path,
                        "operation_id": operation_id,
                        "reason": reason,
                    },
                    now,
                )
        return self.operation(operation_id)

    def operation(self, operation_id: str) -> Operation:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"unknown operation: {operation_id}")
        return self._operation_from_row(row)

    def operations(self, states: Iterable[str] | None = None) -> list[Operation]:
        sql = "SELECT * FROM operations"
        parameters: list[Any] = []
        if states is not None:
            selected = sorted(set(states))
            if not selected:
                return []
            sql += " WHERE state IN (" + ",".join("?" for _ in selected) + ")"
            parameters.extend(selected)
        sql += " ORDER BY opened_seq"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [self._operation_from_row(row) for row in rows]

    def frozen_reason(self, path: str) -> str | None:
        selected = normalize_path(path)
        with self._lock:
            rows = self._connection.execute(
                "SELECT resource_path, reason FROM frozen_resources"
            ).fetchall()
        for row in rows:
            frozen = str(row["resource_path"])
            if path_covers(frozen, selected) or path_covers(selected, frozen):
                return str(row["reason"])
        return None

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> Operation:
        approval = row["approval_json"]
        return Operation(
            operation_id=str(row["operation_id"]),
            device_id=str(row["device_id"]),
            resource_path=str(row["resource_path"]),
            action=str(row["action"]),
            arguments=json.loads(row["arguments_json"]),
            capability_id=str(row["capability_id"]),
            expected_revision=(
                int(row["expected_revision"])
                if row["expected_revision"] is not None
                else None
            ),
            state=str(row["state"]),
            opened_seq=int(row["opened_seq"]),
            dispatched_seq=(
                int(row["dispatched_seq"])
                if row["dispatched_seq"] is not None
                else None
            ),
            deadline=float(row["deadline"]) if row["deadline"] is not None else None,
            error=str(row["error"]) if row["error"] is not None else None,
            approval=json.loads(approval) if approval else None,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
