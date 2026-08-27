"""Agent business processes as supervised user processes.

A ProcessSpec is a systemd-flavoured unit: it declares which namespace paths
it watches (its wake sources), what it does (``handler``), a priority, a
time budget, and a restart policy.  The kernel — not the model — owns the
process skeleton; LLM agents are invoked *inside* the handler and their
failures cannot take the kernel down.  "大模型组织专业能力，而不是替代专业能力。"

Processes never share memory: they interact only through the namespace
(watch/read/derive) and the act() syscall, exactly like processes in an OS
interact only through syscalls and the filesystem.

Time budget: handlers run on a worker thread with a deadline.  A handler
that exceeds its budget is marked failed and its context is *quarantined*
(cancelled) — later syscalls from the abandoned handler raise, like a
process killed by the OOM killer.  (Python threads cannot be force-killed;
the zombie thread is abandoned but harmless.)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class ProcessState(Enum):
    REGISTERED = "registered"
    RUNNING = "running"
    IDLE = "idle"
    BACKOFF = "backoff"
    FAILED = "failed"


class ContextCancelled(RuntimeError):
    pass


class ProcessContext:
    """What a handler may touch.  Handlers run in user space."""

    def __init__(self, kernel, process: "Process"):
        self.kernel = kernel
        self.process = process
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def read(self, path: str):
        return self.kernel.read(path)

    def try_read(self, path: str):
        return self.kernel.try_read(path)

    def act(self, token: str, path: str, action: str, args: Optional[dict] = None, expect: Optional[dict] = None):
        if self._cancelled:
            raise ContextCancelled(f"process {self.process.spec.name} exceeded its budget; syscall refused")
        return self.kernel.act(token, path, action, args or {}, expect)


@dataclass
class ProcessSpec:
    name: str
    watches: tuple[str, ...]
    handler: Callable[[ProcessContext], None]
    priority: int = 0  # higher runs first within a wake cycle
    budget_seconds: float = 10.0  # per-run time budget; overrun = failure
    restart_limit: int = 3
    backoff_seconds: float = 0.0
    description: str = ""


@dataclass
class Process:
    spec: ProcessSpec
    state: ProcessState = ProcessState.REGISTERED
    runs: int = 0
    failures: int = 0
    last_error: Optional[str] = None
    last_run_ts: Optional[float] = None
    _wake_pending: bool = False
    _not_ready_until: float = 0.0


class Scheduler:
    """Event-driven wake-up: namespace commits mark processes runnable."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._processes: dict[str, Process] = {}
        self._cancel_watches: list[Callable[[], None]] = []
        self._cycle_lock = threading.Lock()

    def register(self, spec: ProcessSpec, namespace) -> Process:
        proc = Process(spec=spec)
        self._processes[spec.name] = proc

        def on_commit(snapshot) -> None:
            proc._wake_pending = True

        for sub in spec.watches:
            self._cancel_watches.append(namespace.watch(sub, on_commit))
        return proc

    def processes(self) -> list[Process]:
        return list(self._processes.values())

    def wake(self, name: str) -> None:
        proc = self._processes.get(name)
        if proc is not None:
            proc._wake_pending = True

    # ------------------------------------------------------------------ run

    def run_cycle(self, ctx_factory: Callable[[Process], ProcessContext]) -> list[str]:
        """Run all woken processes once, highest priority first.
        Multiple wakes inside one cycle coalesce into a single run.
        Returns names of processes that completed without error.
        Only one cycle runs at a time across all pump threads."""
        if not self._cycle_lock.acquire(blocking=False):
            return []
        try:
            return self._run_cycle(ctx_factory)
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, ctx_factory: Callable[[Process], ProcessContext]) -> list[str]:
        runnable = [p for p in self._processes.values() if p._wake_pending and p.state is not ProcessState.FAILED]
        runnable.sort(key=lambda p: -p.spec.priority)
        ran: list[str] = []
        now = self._clock()
        for proc in runnable:
            proc._wake_pending = False
            if proc.state is ProcessState.BACKOFF and now < proc._not_ready_until:
                proc._wake_pending = True  # keep it scheduled for later
                continue
            proc.state = ProcessState.RUNNING
            error = self._run_one(proc, ctx_factory(proc))
            if error is None:
                proc.failures = 0
                proc.state = ProcessState.IDLE
                ran.append(proc.spec.name)
            else:
                proc.failures += 1
                proc.last_error = error
                if proc.failures > proc.spec.restart_limit:
                    proc.state = ProcessState.FAILED
                else:
                    proc.state = ProcessState.BACKOFF
                    proc._not_ready_until = now + proc.spec.backoff_seconds
            proc.runs += 1
            proc.last_run_ts = now
        return ran

    def _run_one(self, proc: Process, ctx: ProcessContext) -> Optional[str]:
        outcome: dict = {"error": None}

        def target() -> None:
            try:
                proc.spec.handler(ctx)
            except Exception as exc:  # noqa: BLE001 — supervisor must survive user space
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        worker = threading.Thread(target=target, daemon=True, name=f"dos-proc-{proc.spec.name}")
        worker.start()
        worker.join(proc.spec.budget_seconds)
        if worker.is_alive():
            ctx._cancelled = True  # quarantine: further syscalls from it raise
            return f"budget exceeded ({proc.spec.budget_seconds}s); handler abandoned"
        return outcome["error"]
