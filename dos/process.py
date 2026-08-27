"""Agent business processes as supervised user processes.

A ProcessSpec is a systemd-flavoured unit: it declares which namespace paths
it watches (its wake sources), what it does (``handler``), a priority, and
a restart policy.  The kernel — not the model — owns the process skeleton;
LLM agents are invoked *inside* the handler and their failures cannot take
the process definition down.  "大模型组织专业能力，而不是替代专业能力。"

Processes never share memory: they interact only through the namespace
(watch/read/derive) and the act() syscall, exactly like processes in an OS
interact only through syscalls and the filesystem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class ProcessState(Enum):
    REGISTERED = "registered"
    RUNNING = "running"
    IDLE = "idle"
    BACKOFF = "backoff"
    FAILED = "failed"


class ProcessContext:
    """What a handler may touch.  Handlers run in user space."""

    def __init__(self, kernel, process: "Process"):
        self.kernel = kernel
        self.process = process

    def read(self, path: str):
        return self.kernel.read(path)

    def try_read(self, path: str):
        return self.kernel.try_read(path)

    def act(self, token: str, path: str, action: str, args: Optional[dict] = None, expect: Optional[dict] = None):
        return self.kernel.act(token, path, action, args or {}, expect)


@dataclass
class ProcessSpec:
    name: str
    watches: tuple[str, ...]
    handler: Callable[[ProcessContext], None]
    priority: int = 0  # higher runs first within a wake cycle
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
        Returns names of processes that ran."""
        runnable = [p for p in self._processes.values() if p._wake_pending and p.state != ProcessState.FAILED]
        runnable.sort(key=lambda p: -p.spec.priority)
        ran: list[str] = []
        now = self._clock()
        for proc in runnable:
            proc._wake_pending = False
            if proc.state is ProcessState.BACKOFF and now < proc._not_ready_until:
                proc._wake_pending = True  # keep it scheduled for later
                continue
            proc.state = ProcessState.RUNNING
            try:
                ctx = ctx_factory(proc)
                proc.spec.handler(ctx)
                proc.failures = 0
                proc.state = ProcessState.IDLE
            except Exception as exc:  # noqa: BLE001 — supervisor must survive user space
                proc.failures += 1
                proc.last_error = f"{type(exc).__name__}: {exc}"
                if proc.failures > proc.spec.restart_limit:
                    proc.state = ProcessState.FAILED
                else:
                    proc.state = ProcessState.BACKOFF
                    proc._not_ready_until = now + proc.spec.backoff_seconds
            proc.runs += 1
            proc.last_run_ts = now
            if proc.state is not ProcessState.FAILED:
                ran.append(proc.spec.name)
        return ran
