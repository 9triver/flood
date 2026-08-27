from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .driver import Driver
from .errors import (
    FrozenResourceError,
    InvalidActionError,
    OperationConflictError,
    PreconditionError,
)
from .model import (
    ActResult,
    Capability,
    Operation,
    Snapshot,
    canonical_json,
    normalize_path,
    path_covers,
)
from .process import ProcessContext, ProcessSpec
from .store import SQLiteStore


class Kernel:
    """Single-node MVP kernel.

    The SQLite ledger is durable. Drivers and deterministic processes are
    code and must be mounted again after a restart. Dispatched operations are
    recovered for reconciliation only; the kernel never dispatches them again.
    """

    def __init__(
        self,
        database: str | Path = ":memory:",
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.clock = clock
        self.store = SQLiteStore(database)
        self._drivers: dict[str, Driver] = {}
        self._mounts: dict[str, str] = {}
        self._processes: dict[str, ProcessSpec] = {}
        self._interrupts: list[tuple[str, object]] = []
        self._lock = threading.RLock()
        self._pump_lock = threading.Lock()
        self._watch_condition = threading.Condition()
        self._stop = threading.Event()

    # ------------------------------------------------------------- mounting

    def mount(self, prefix: str, driver: Driver) -> None:
        prefix = normalize_path(prefix)
        if not driver.device_id:
            raise ValueError("driver.device_id is required")
        with self._lock:
            if driver.device_id in self._drivers:
                raise ValueError(f"device already mounted: {driver.device_id}")
            for existing in self._mounts:
                if path_covers(existing, prefix) or path_covers(prefix, existing):
                    raise ValueError(
                        f"mount {prefix} overlaps existing mount {existing}"
                    )
            driver.attach(self)
            self._drivers[driver.device_id] = driver
            self._mounts[prefix] = driver.device_id

    def spawn(self, spec: ProcessSpec) -> None:
        with self._lock:
            if spec.name in self._processes:
                raise ValueError(f"process already exists: {spec.name}")
            if not spec.watches:
                raise ValueError("an MVP process must watch at least one path")
            self._processes[spec.name] = ProcessSpec(
                name=spec.name,
                watches=tuple(normalize_path(path) for path in spec.watches),
                handler=spec.handler,
            )

    # ---------------------------------------------------------- capabilities

    def grant(
        self,
        principal: str,
        prefix: str,
        actions,
        *,
        expires_at: float | None = None,
    ) -> Capability:
        return self.store.grant_capability(
            principal,
            prefix,
            actions,
            expires_at=expires_at,
            now=self.clock(),
        )

    # ------------------------------------------------------------- syscalls

    def read(self, path: str) -> Snapshot | None:
        return self.store.read(path)

    def history(
        self,
        path: str,
        *,
        since: float | None = None,
        after_seq: int = 0,
    ):
        return self.store.history(path, since=since, after_seq=after_seq)

    def watch(
        self,
        path: str,
        *,
        after_revision: int = 0,
        timeout: float | None = None,
    ) -> Snapshot | None:
        """Wait for one exact resource path to advance beyond a revision."""
        selected = normalize_path(path)
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self._watch_condition:
            while True:
                snapshot = self.read(selected)
                if snapshot is not None and snapshot.revision > after_revision:
                    return snapshot
                if deadline is None:
                    self._watch_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._watch_condition.wait(remaining)

    def act(
        self,
        capability_token: str,
        path: str,
        action: str,
        arguments: dict[str, Any] | None = None,
        *,
        expected_revision: int | None = None,
    ) -> ActResult:
        resource_path = normalize_path(path)
        arguments = dict(arguments or {})
        with self._lock:
            frozen_reason = self.store.frozen_reason(resource_path)
            if frozen_reason:
                raise FrozenResourceError(
                    f"{resource_path} is frozen: {frozen_reason}"
                )

            capability = self.store.check_capability(
                capability_token,
                resource_path,
                action,
                now=self.clock(),
            )
            driver = self._driver_for(resource_path)
            invalid = driver.validate(resource_path, action, arguments)
            if invalid:
                raise InvalidActionError(invalid)

            active = self.store.active_operation(resource_path)
            if active is not None:
                if (
                    active.action == action
                    and canonical_json(active.arguments) == canonical_json(arguments)
                ):
                    return ActResult(
                        operation_id=active.operation_id,
                        state=active.state,
                        reused=True,
                    )
                raise OperationConflictError(
                    f"resource already has active operation {active.operation_id}"
                )

            if expected_revision is not None:
                snapshot = self.read(resource_path)
                actual_revision = snapshot.revision if snapshot is not None else 0
                if actual_revision != int(expected_revision):
                    raise PreconditionError(
                        f"expected revision {expected_revision} for {resource_path}, "
                        f"current revision is {actual_revision}"
                    )

            now = self.clock()
            privileged = action in driver.privileged_actions
            initial_state = "awaiting_approval" if privileged else "dispatched"
            timeout = driver.deadline_for(action)
            deadline = (
                now + float(timeout)
                if not privileged and timeout is not None
                else None
            )
            operation = self.store.create_operation(
                operation_id=f"op_{secrets.token_hex(8)}",
                device_id=driver.device_id,
                resource_path=resource_path,
                action=action,
                arguments=arguments,
                capability_id=capability.capability_id,
                expected_revision=expected_revision,
                initial_state=initial_state,
                deadline=deadline,
                now=now,
            )
            if operation.state == "dispatched":
                operation = self._dispatch(operation, driver)
            return ActResult(operation.operation_id, operation.state)

    def approve(
        self,
        operation_id: str,
        *,
        approved_by: str,
        decision: bool,
        reason: str = "",
    ) -> ActResult:
        with self._lock:
            operation = self.store.operation(operation_id)
            driver = self._drivers.get(operation.device_id)
            if driver is None:
                raise RuntimeError(
                    f"device is not mounted: {operation.device_id}"
                )
            timeout = driver.deadline_for(operation.action)
            deadline = (
                self.clock() + float(timeout)
                if decision and timeout is not None
                else None
            )
            operation = self.store.resolve_approval(
                operation_id,
                approved_by=approved_by,
                decision=decision,
                reason=reason,
                deadline=deadline,
                now=self.clock(),
            )
            if decision:
                operation = self._dispatch(operation, driver)
            return ActResult(operation.operation_id, operation.state)

    def operation(self, operation_id: str) -> Operation:
        return self.store.operation(operation_id)

    def operations(self, states=None) -> list[Operation]:
        return self.store.operations(states)

    # ------------------------------------------------------------ interrupts

    def interrupt(self, device_id: str, raw: object) -> None:
        with self._lock:
            if device_id not in self._drivers:
                raise KeyError(f"unknown device: {device_id}")
            self._interrupts.append((device_id, raw))

    def pump(self, *, interrupt_limit: int = 1000) -> dict[str, Any]:
        """Run one deterministic turn: ingest, reconcile, then processes."""
        with self._pump_lock:
            with self._lock:
                interrupts = self._interrupts[:interrupt_limit]
                del self._interrupts[:interrupt_limit]

            changed_paths: set[str] = set()
            accepted = 0
            rejected = 0
            for device_id, raw in interrupts:
                driver = self._drivers[device_id]
                try:
                    normalized = list(driver.normalize(raw))
                    mount = self._mount_for_device(device_id)
                    for item in normalized:
                        path = normalize_path(item.path)
                        if not path_covers(mount, path):
                            raise ValueError(
                                f"driver {device_id} emitted outside {mount}: {path}"
                            )
                        _, projected = self.store.record_observation(
                            path,
                            item.value,
                            item.observed_at,
                            item.source,
                            self.clock(),
                        )
                        accepted += 1
                        if projected:
                            changed_paths.add(path)
                except Exception as exc:  # invalid device input is quarantined
                    rejected += 1
                    self.store.record_system_event(
                        "driver_input_rejected",
                        {
                            "device_id": device_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        self.clock(),
                    )

            if changed_paths:
                with self._watch_condition:
                    self._watch_condition.notify_all()

            reconciled = self._reconcile()
            ran = self._run_processes(changed_paths)
            return {
                "interrupts": len(interrupts),
                "observations": accepted,
                "rejected": rejected,
                "reconciled": reconciled,
                "ran": ran,
            }

    # -------------------------------------------------------------- runtime

    def run(self, *, idle_seconds: float = 0.05) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            stats = self.pump()
            if not stats["interrupts"] and not stats["ran"]:
                self._stop.wait(idle_seconds)

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()
        self.store.close()

    # -------------------------------------------------------------- helpers

    def _dispatch(self, operation: Operation, driver: Driver) -> Operation:
        """The dispatched fence is already durable before this method runs."""
        try:
            driver.dispatch(operation)
        except Exception as exc:
            return self.store.transition_operation(
                operation.operation_id,
                "unknown",
                error=f"dispatch raised {type(exc).__name__}: {exc}",
                now=self.clock(),
                freeze=True,
            )
        return operation

    def _reconcile(self) -> list[str]:
        outcomes = []
        for operation in self.store.operations({"dispatched"}):
            driver = self._drivers.get(operation.device_id)
            if driver is None:
                continue
            evidence = self.store.observations_after(
                int(operation.dispatched_seq or operation.opened_seq)
            )
            try:
                verification = driver.verify(operation, evidence)
            except Exception as exc:
                self.store.record_system_event(
                    "verification_failed",
                    {
                        "operation_id": operation.operation_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    self.clock(),
                )
                verification = None

            if verification is not None and verification.state == "committed":
                self.store.transition_operation(
                    operation.operation_id,
                    "committed",
                    error=None,
                    now=self.clock(),
                )
                outcomes.append(f"{operation.operation_id}:committed")
                continue
            if verification is not None and verification.state == "failed":
                self.store.transition_operation(
                    operation.operation_id,
                    "failed",
                    error=verification.error or "device evidence refuted operation",
                    now=self.clock(),
                )
                outcomes.append(f"{operation.operation_id}:failed")
                continue
            if operation.deadline is not None and self.clock() >= operation.deadline:
                self.store.transition_operation(
                    operation.operation_id,
                    "unknown",
                    error="deadline passed without conclusive device evidence",
                    now=self.clock(),
                    freeze=True,
                )
                outcomes.append(f"{operation.operation_id}:unknown")
        return outcomes

    def _run_processes(self, changed_paths: set[str]) -> list[str]:
        if not changed_paths:
            return []
        with self._lock:
            specs = list(self._processes.values())
        ran = []
        for spec in specs:
            if not any(
                path_covers(watch, changed)
                for watch in spec.watches
                for changed in changed_paths
            ):
                continue
            try:
                spec.handler(ProcessContext(self))
            except Exception as exc:
                self.store.record_system_event(
                    "process_failed",
                    {
                        "process": spec.name,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    self.clock(),
                )
            ran.append(spec.name)
        return ran

    def _driver_for(self, path: str) -> Driver:
        for prefix, device_id in self._mounts.items():
            if path_covers(prefix, path):
                return self._drivers[device_id]
        raise KeyError(f"no device mounted for {path}")

    def _mount_for_device(self, device_id: str) -> str:
        for prefix, selected in self._mounts.items():
            if selected == device_id:
                return prefix
        raise KeyError(device_id)
