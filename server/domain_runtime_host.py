"""Own an asynchronous DomainRuntime on one dedicated event-loop thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Protocol, TypeVar


class HostedDomainSystem(Protocol):
    runtime: Any

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


CloseCallback = Callable[[], None]
DomainSystemFactory = Callable[
    [],
    tuple[HostedDomainSystem, CloseCallback | None],
]

T = TypeVar("T")


class DomainRuntimeHostError(RuntimeError):
    pass


class DomainRuntimeHost:
    """Create, start, call and stop a domain system on one event loop."""

    def __init__(
        self,
        factory: DomainSystemFactory,
        *,
        start_timeout: float = 30.0,
        call_timeout: float = 360.0,
        stop_timeout: float = 30.0,
    ) -> None:
        self.factory = factory
        self.start_timeout = float(start_timeout)
        self.call_timeout = float(call_timeout)
        self.stop_timeout = float(stop_timeout)
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._system: HostedDomainSystem | None = None
        self._start_error: BaseException | None = None
        self._stop_error: BaseException | None = None

    @property
    def runtime(self) -> Any:
        if self._system is None or self._thread is None or not self._thread.is_alive():
            raise DomainRuntimeHostError("domain runtime host is not running")
        return self._system.runtime

    @property
    def read_model(self) -> HostedDomainReadModel:
        self.runtime
        return HostedDomainReadModel(self)

    @property
    def thread_id(self) -> int | None:
        return self._thread.ident if self._thread is not None else None

    def start(self) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                return
            raise DomainRuntimeHostError("domain runtime host cannot be restarted")
        self._thread = threading.Thread(
            target=self._thread_main,
            name="domain-runtime-host",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=self.start_timeout):
            raise DomainRuntimeHostError("domain runtime host start timed out")
        if self._start_error is not None:
            raise DomainRuntimeHostError(
                f"domain runtime host failed to start: {self._start_error}"
            ) from self._start_error
        if not self._thread.is_alive() or self._system is None:
            raise DomainRuntimeHostError("domain runtime host stopped during start")

    def call(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        timeout: float | None = None,
    ) -> T:
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None or not thread.is_alive():
            raise DomainRuntimeHostError("domain runtime host is not running")
        if threading.get_ident() == thread.ident:
            raise DomainRuntimeHostError(
                "domain runtime host cannot synchronously call its own event-loop thread"
            )

        async def invoke() -> T:
            return await operation()

        future = asyncio.run_coroutine_threadsafe(invoke(), loop)
        wait_seconds = self.call_timeout if timeout is None else float(timeout)
        try:
            return future.result(timeout=wait_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"domain runtime operation exceeded {wait_seconds:g}s"
            ) from exc

    def call_system(
        self,
        operation: Callable[[HostedDomainSystem], Awaitable[T]],
        *,
        timeout: float | None = None,
    ) -> T:
        async def invoke() -> T:
            system = self._system
            if system is None:
                raise DomainRuntimeHostError("domain runtime host is not running")
            return await operation(system)

        return self.call(invoke, timeout=timeout)

    def stop(self) -> None:
        thread = self._thread
        loop = self._loop
        if thread is None:
            return
        if thread.is_alive() and loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=self.stop_timeout)
        if thread.is_alive():
            raise DomainRuntimeHostError("domain runtime host stop timed out")
        if self._stop_error is not None:
            raise DomainRuntimeHostError(
                f"domain runtime host failed to stop cleanly: {self._stop_error}"
            ) from self._stop_error

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        system: HostedDomainSystem | None = None
        close: CloseCallback | None = None
        started = False
        try:
            system, close = self.factory()
            loop.run_until_complete(system.start())
            started = True
            self._system = system
        except BaseException as exc:
            self._start_error = exc
        if self._start_error is None:
            self._ready.set()

        try:
            if self._start_error is None:
                loop.run_forever()
        finally:
            try:
                if started and system is not None:
                    loop.run_until_complete(system.stop())
            except BaseException as exc:
                self._stop_error = exc
            try:
                if close is not None:
                    close()
            except BaseException as exc:
                if self._stop_error is None:
                    self._stop_error = exc
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True),
                )
            loop.close()
            self._loop = None
            self._system = None
            self._ready.set()


class HostedDomainReadModel:
    """Marshal synchronous read-model calls onto the runtime owner loop."""

    def __init__(self, host: DomainRuntimeHost) -> None:
        self.host = host

    @property
    def domain_id(self) -> str:
        return str(self._call("domain_id"))

    def resources(self, *, resource_type: str | None = None):
        return self._call("resources", resource_type=resource_type)

    def projection(self, resource_id: str):
        return self._call("projection", resource_id)

    def products(
        self,
        *,
        product_type: str | None = None,
        subject_id: str | None = None,
    ):
        return self._call(
            "products",
            product_type=product_type,
            subject_id=subject_id,
        )

    def events(self, *, event_type: str | None = None):
        return self._call("events", event_type=event_type)

    def commands(self):
        return self._call("commands")

    def subscribe(self, handler, *, event_type: str | None = None):
        dispose = self._call("subscribe", handler, event_type=event_type)
        disposed = False
        lock = threading.Lock()

        def hosted_dispose() -> None:
            nonlocal disposed
            with lock:
                if disposed:
                    return
                disposed = True

            async def invoke_dispose() -> None:
                dispose()

            self.host.call(invoke_dispose)

        return hosted_dispose

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        async def invoke() -> Any:
            target = self.host.runtime
            attribute = getattr(target, name)
            if not callable(attribute):
                if args or kwargs:
                    raise TypeError(f"runtime attribute is not callable: {name}")
                return attribute
            return attribute(*args, **kwargs)

        return self.host.call(invoke)


__all__ = [
    "DomainRuntimeHost",
    "DomainRuntimeHostError",
    "DomainSystemFactory",
    "HostedDomainSystem",
    "HostedDomainReadModel",
]
