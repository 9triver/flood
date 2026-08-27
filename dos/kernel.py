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
3. precondition       ``expect`` (path -> value) must still hold (CAS)
4. journal            the intent is durably recorded before any effect
5. privilege gate     privileged actions park as ``awaiting_approval``
                      until ``approve()`` (pkexec) — itself journaled
6. dispatch           the driver sends the command; a pending transaction
                      with a deadline is opened; reality is later confirmed
                      by telemetry via the consistency checker

Pump model
----------
The kernel is deterministic and single-logical-threaded: ``pump()``
drains interrupts (normalize -> journal -> namespace commit -> wake),
re-verifies pending transactions, expires deadlines, and runs one
scheduler cycle.  ``run()`` loops over ``pump()``.  No callback into the
kernel may re-enter it; interrupts raised from drivers simply queue.
"""

from __future__ import annotations

import itertools
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .capabilities import CapabilityError, CapabilityRegistry
from .consistency import Consistency
from .devices import Driver, PendingTxn
from .journal import Journal
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

    @property
    def awaiting_approval(self) -> bool:
        return self.state == "awaiting_approval"


class Kernel:
    def __init__(self, journal: Optional[Journal] = None, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.journal = journal if journal is not None else Journal(clock=clock)
        self.namespace = Namespace()
        self.caps = CapabilityRegistry()
        self.consistency = Consistency(self.journal, clock)
        self.scheduler = Scheduler(clock=clock)
        self.drivers: dict[str, Driver] = {}
        self._mounts: dict[str, str] = {}  # device_id -> mount prefix
        self._txn_seq = itertools.count(1)
        self._interrupts: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------- mounting

    def mount(self, prefix: str, driver: Driver) -> None:
        prefix = "/" + "/".join(p for p in prefix.split("/") if p)
        if not driver.device_id:
            driver.device_id = prefix
        driver.attach(self)
        self.drivers[driver.device_id] = driver
        self._mounts[driver.device_id] = prefix
        self.journal.append("note", {"event": "mount", "device_id": driver.device_id, "prefix": prefix})

    def grant(self, prefix: str, actions, granted_by: str, description: str = ""):
        return self.caps.grant(prefix, actions, granted_by, description)

    # ------------------------------------------------------------ interrupts

    def interrupt(self, device_id: str, raw: object) -> None:
        """Top half: register a raw interrupt.  Cheap, safe, no parsing."""
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

        frozen_reason = self.consistency.is_frozen(path)
        if frozen_reason:
            raise FrozenPathError(f"{path} is frozen: {frozen_reason}")

        cap = self.caps.check(token, path, action)
        device = self._device_for(path)

        if expect:
            for exp_path, exp_value in expect.items():
                snap = self.namespace.try_read(exp_path)
                if snap is None or snap.value != exp_value:
                    raise PreconditionError(f"precondition failed for {exp_path}: expected {exp_value!r}, world is {snap.value if snap else None!r}")

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
            self.consistency.set_state(txn, "awaiting_approval")
            return ActResult(txn_id=txn.txn_id, state=txn.state)

        self._dispatch(txn, device)
        return ActResult(txn_id=txn.txn_id, state=txn.state)

    def approve(self, txn_id: str, approved_by: str, decision: bool, reason: str = "") -> ActResult:
        """pkexec: resolve a parked privileged syscall."""
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

        # fsck: did new evidence resolve pending transactions?
        self.consistency.check(self.namespace.try_read, self.drivers)
        self.consistency.expire(self.drivers)

        # scheduler: one supervised cycle for woken processes
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
        self.consistency.set_state(txn, "dispatched")
        record = self.journal.append("txn", {"txn_id": txn.txn_id, "event": "dispatch", "device_id": driver.device_id})
        txn.dispatched_seq = record.seq
        driver.dispatch(txn)

    def _device_for(self, path: str) -> Driver:
        best = None
        best_len = -1
        for device_id, prefix in self._mounts.items():
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                if len(prefix) > best_len:
                    best, best_len = self.drivers[device_id], len(prefix)
        if best is None:
            raise KeyError(f"no device mounted for {path}")
        return best
