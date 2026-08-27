"""The kernel: mounts devices, owns the one trusted namespace, and exposes
the syscall surface to agents.

Syscalls
--------
``read(path)``          snapshot + generation (staleness is detectable)
``watch(sub, cb)``      inotify-style subscription
``derive(path, deps, fn)`` register a page-cache-like view
``act(token, path, action, args, expect)``  the only mutating syscall

``act()`` pipeline — the kernel does not trust user space:

1. freeze check       the path may be frozen by an unknown outcome
2. capability check   the token must permit action-on-path
3. idempotency        an identical in-flight transaction is reused, not
                      duplicated (retrying a pending syscall returns the
                      same handle)
4. precondition       ``expect`` (path -> value) must still hold (CAS)
5. journal            the intent is durably recorded before any effect
6. privilege gate     privileged actions park as ``awaiting_approval``
                      until ``approve()`` (pkexec) — itself journaled;
                      approval has a timeout
7. dispatch           the driver sends the command; a pending transaction
                      with a deadline is opened; reality is confirmed only
                      by telemetry evidence that is *newer than the
                      dispatch* (stale evidence never commits a txn)

Concurrency
-----------
One big reentrant lock serializes every *individual* syscall and every
kernel-side section of the pump (drain, fsck) — like a kernel holding the
big lock only while inside the kernel.  The lock is dropped while user
processes run, so handlers (on worker threads, for time budgets) can make
syscalls freely; a syscall from any thread is atomic, syscalls from
different threads may interleave — exactly processes racing syscalls in a
real OS.  Two pumps never run a scheduler cycle concurrently (the
scheduler guards itself).
"""

from __future__ import annotations

import itertools
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .capabilities import Capability, CapabilityError, CapabilityRegistry
from .consistency import Consistency
from .devices import Driver, PendingTxn
from .journal import Journal, Record
from .namespace import Namespace, NotFound, Snapshot
from .process import Process, ProcessContext, ProcessSpec, Scheduler


class FrozenPathError(RuntimeError):
    pass


class PreconditionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActResult:
    txn_id: str
    state: str  # dispatched | awaiting_approval | ...
    reused: bool = False  # idempotent hit on an in-flight transaction

    @property
    def awaiting_approval(self) -> bool:
        return self.state == "awaiting_approval"


class Kernel:
    def __init__(
        self,
        journal: Optional[Journal] = None,
        clock: Callable[[], float] = time.time,
        default_approval_timeout: float = 300.0,
    ):
        self.clock = clock
        self.journal = journal if journal is not None else Journal(clock=clock)
        self.namespace = Namespace()
        self.caps = CapabilityRegistry()
        self.consistency = Consistency(self.journal, clock)
        self.scheduler = Scheduler(clock=clock)
        self.drivers: dict[str, Driver] = {}
        self._mounts: dict[str, str] = {}  # mount prefix -> device_id
        self._txn_seq = itertools.count(1)
        self._interrupts: list[tuple[str, object]] = []
        self._lock = threading.RLock()  # the kernel big lock
        self._running = False
        self.default_approval_timeout = default_approval_timeout

    # ------------------------------------------------------------- mounting

    def mount(self, prefix: str, driver: Driver) -> None:
        prefix = "/" + "/".join(p for p in prefix.split("/") if p)
        if prefix in self._mounts:
            raise RuntimeError(f"prefix {prefix} already mounted by {self._mounts[prefix]}")
        if not driver.device_id:
            driver.device_id = prefix
        if driver.device_id in self.drivers:
            raise RuntimeError(f"device {driver.device_id} already mounted")
        driver.attach(self)
        self.drivers[driver.device_id] = driver
        self._mounts[prefix] = driver.device_id
        self.journal.append("note", {"event": "mount", "device_id": driver.device_id, "prefix": prefix})

    def grant(self, prefix: str, actions, granted_by: str, description: str = "") -> Capability:
        cap = self.caps.grant(prefix, actions, granted_by, description)
        self.journal.append(
            "capability",
            {
                "event": "grant",
                "token": cap.token,
                "prefix": cap.prefix,
                "actions": sorted(cap.actions),
                "granted_by": granted_by,
                "description": description,
            },
        )
        return cap

    # ------------------------------------------------------------ interrupts

    def interrupt(self, device_id: str, raw: object) -> None:
        """Top half: register a raw interrupt.  Cheap, safe, no parsing."""
        with self._lock:
            self._interrupts.append((device_id, raw))

    # ------------------------------------------------------------- syscalls

    def read(self, path: str) -> Snapshot:
        return self.namespace.read(path)

    def try_read(self, path: str) -> Optional[Snapshot]:
        return self.namespace.try_read(path)

    def watch(self, subscription: str, callback: Callable[[Snapshot], None]) -> Callable[[], None]:
        return self.namespace.watch(subscription, callback)

    def derive(self, path: str, depends_on: tuple[str, ...], fn: Callable[[Namespace], object]) -> None:
        self.namespace.derive(path, depends_on, fn)

    def act(
        self,
        token: str,
        path: str,
        action: str,
        args: Optional[dict] = None,
        expect: Optional[dict] = None,
    ) -> ActResult:
        args = args or {}
        path = "/" + "/".join(p for p in path.split("/") if p)

        with self._lock:
            frozen_reason = self.consistency.is_frozen(path)
            if frozen_reason:
                raise FrozenPathError(f"{path} is frozen: {frozen_reason}")

            cap = self.caps.check(token, path, action)
            device = self._device_for(path)

            existing = self.consistency.find_pending(device.device_id, path, action, args, expect)
            if existing is not None:
                return ActResult(txn_id=existing.txn_id, state=existing.state, reused=True)

            if expect:
                for exp_path, exp_value in expect.items():
                    snap = self.namespace.try_read(exp_path)
                    if snap is None or snap.value != exp_value:
                        raise PreconditionError(
                            f"precondition failed for {exp_path}: expected {exp_value!r}, "
                            f"world is {snap.value if snap else None!r}"
                        )

            txn = PendingTxn(
                txn_id=f"txn_{next(self._txn_seq)}_{secrets.token_hex(4)}",
                device_id=device.device_id,
                path=path,
                action=action,
                args=args,
                expect=expect,
                opened_seq=self.journal.last_seq,
            )
            self.consistency.open(txn)

            if action in device.privileged_actions:
                txn.approval_deadline = self.clock() + self.default_approval_timeout
                self.consistency.set_state(txn, "awaiting_approval")
                return ActResult(txn_id=txn.txn_id, state=txn.state)

            self._dispatch(txn, device)
            return ActResult(txn_id=txn.txn_id, state=txn.state)

    def approve(self, txn_id: str, approved_by: str, decision: bool, reason: str = "") -> ActResult:
        """pkexec: resolve a parked privileged syscall."""
        with self._lock:
            txn = self.consistency.get(txn_id)
            if txn.state != "awaiting_approval":
                raise RuntimeError(f"txn {txn_id} is not awaiting approval (state={txn.state})")
            record = self.journal.append(
                "approval",
                {"txn_id": txn_id, "approved_by": approved_by, "decision": decision, "reason": reason},
            )
            if not decision:
                self.consistency.set_state(txn, "failed", error="rejected by approver")
                return ActResult(txn_id=txn.txn_id, state=txn.state)
            txn.approval = {"approved_by": approved_by, "seq": record.seq}
            self._dispatch(txn, self.drivers[txn.device_id])
            return ActResult(txn_id=txn.txn_id, state=txn.state)

    def txn(self, txn_id: str) -> PendingTxn:
        return self.consistency.get(txn_id)

    # ------------------------------------------------------------ processes

    def spawn(self, spec: ProcessSpec) -> Process:
        return self.scheduler.register(spec, self.namespace)

    def wake(self, process_name: str) -> None:
        self.scheduler.wake(process_name)

    # ------------------------------------------------------------ the pump

    def pump(self, interrupt_limit: int = 1000) -> dict:
        """One deterministic kernel turn."""
        stats = {"commits": 0, "interrupts": 0, "ran": []}

        with self._lock:
            interrupts, self._interrupts = self._interrupts[:interrupt_limit], self._interrupts[interrupt_limit:]
            # bottom halves
            for device_id, raw in interrupts:
                driver = self.drivers[device_id]
                for path, value in driver.normalize(raw):
                    record = self.journal.append("observation", {"device_id": device_id, "path": path, "value": value})
                    self.namespace.write(path, value, source_seq=record.seq, ts=record.ts)
                    stats["commits"] += 1
                stats["interrupts"] += 1

            # fsck: did *new* evidence resolve pending transactions?
            self.consistency.check(self.namespace.try_read, self.drivers)
            self.consistency.expire(self.drivers)

        # drop the big lock while user processes run: their syscalls take
        # the lock per-call, so a handler can act() without deadlocking
        stats["ran"] = self.scheduler.run_cycle(lambda proc: ProcessContext(self, proc))
        return stats

    def run(self, idle_seconds: float = 0.05, max_cycles: Optional[int] = None) -> None:
        """Foreground loop: pump until stopped."""
        self._running = True
        cycles = 0
        while self._running:
            stats = self.pump()
            cycles += 1
            if max_cycles and cycles >= max_cycles:
                break
            if not stats["interrupts"] and not stats["ran"]:
                time.sleep(idle_seconds)

    def stop(self) -> None:
        self._running = False

    # -------------------------------------------------------------- helpers

    def _dispatch(self, txn: PendingTxn, driver: Driver) -> None:
        txn.deadline = self.clock() + driver.default_txn_timeout if driver.default_txn_timeout else None
        record = self.consistency.set_state(txn, "dispatched", extra={"device_id": driver.device_id})
        txn.dispatched_seq = record.seq
        driver.dispatch(txn)

    def _device_for(self, path: str) -> Driver:
        best = None
        best_len = -1
        for prefix, device_id in self._mounts.items():
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                if len(prefix) > best_len:
                    best, best_len = self.drivers[device_id], len(prefix)
        if best is None:
            raise KeyError(f"no device mounted for {path}")
        return best
