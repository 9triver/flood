"""Transport contracts and deterministic in-memory adapters for drivers."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import uuid4

import paho.mqtt.client as mqtt


MessageHandler = Callable[[str, bytes], Awaitable[None]]


@runtime_checkable
class MqttTransport(Protocol):
    connected: bool

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def subscribe(self, topic_filter: str, handler: MessageHandler) -> None: ...

    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> None: ...


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    topic: str
    payload: bytes
    qos: int


class InMemoryMqttTransport:
    """MQTT-shaped transport used to validate drivers without a broker."""

    def __init__(self) -> None:
        self.connected = False
        self.published: list[PublishedMessage] = []
        self._subscriptions: list[tuple[str, MessageHandler]] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        self._subscriptions.clear()

    async def subscribe(self, topic_filter: str, handler: MessageHandler) -> None:
        if not self.connected:
            raise RuntimeError("MQTT transport is not connected")
        self._subscriptions.append((topic_filter, handler))

    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> None:
        if not self.connected:
            raise RuntimeError("MQTT transport is not connected")
        self.published.append(PublishedMessage(topic, bytes(payload), int(qos)))

    async def inject(self, topic: str, payload: bytes) -> None:
        if not self.connected:
            raise RuntimeError("MQTT transport is not connected")
        for topic_filter, handler in tuple(self._subscriptions):
            if _topic_matches(topic_filter, topic):
                await handler(topic, bytes(payload))


class PahoMqttTransport:
    """Asyncio-facing MQTT transport backed by Eclipse Paho."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 8883,
        tls: bool = True,
        ca_file: str | None = None,
        username: str | None = None,
        password: str | None = None,
        client_id: str | None = None,
        keepalive: int = 60,
        connect_timeout: float = 15.0,
        operation_timeout: float = 10.0,
    ) -> None:
        self.host = str(host or "").strip()
        if not self.host:
            raise ValueError("MQTT host must not be empty")
        if port < 1 or port > 65535:
            raise ValueError("MQTT port must be between 1 and 65535")
        if keepalive < 1:
            raise ValueError("MQTT keepalive must be positive")
        self.port = int(port)
        self.tls = bool(tls)
        self.ca_file = ca_file
        self.username = username
        self.password = password
        self.client_id = client_id or f"agent-domain-os-{uuid4().hex[:12]}"
        self.keepalive = int(keepalive)
        self.connect_timeout = float(connect_timeout)
        self.operation_timeout = float(operation_timeout)
        self.connected = False
        self.last_error: str | None = None
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_waiter: asyncio.Future[None] | None = None
        self._subscribe_waiters: dict[int, asyncio.Future[None]] = {}
        self._subscriptions: dict[str, list[MessageHandler]] = {}

    async def connect(self) -> None:
        if self.connected:
            return
        if self._client is not None:
            raise RuntimeError("MQTT connection attempt is already active")

        loop = asyncio.get_running_loop()
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        if self.username is not None:
            client.username_pw_set(self.username, self.password)
        if self.tls:
            client.tls_set(
                ca_certs=self.ca_file,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )

        self._loop = loop
        self._client = client
        self._connect_waiter = loop.create_future()
        client.connect_async(
            self.host,
            port=self.port,
            keepalive=self.keepalive,
        )
        client.loop_start()
        try:
            await asyncio.wait_for(
                asyncio.shield(self._connect_waiter),
                timeout=self.connect_timeout,
            )
        except BaseException:
            await self._close_client()
            raise

    async def disconnect(self) -> None:
        await self._close_client()
        self._subscriptions.clear()

    async def subscribe(self, topic_filter: str, handler: MessageHandler) -> None:
        client = self._connected_client()
        selected_filter = _validate_topic_filter(topic_filter)
        handlers = self._subscriptions.setdefault(selected_filter, [])
        first_handler = not handlers
        handlers.append(handler)
        if not first_handler:
            return
        result, message_id = client.subscribe(selected_filter, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            handlers.remove(handler)
            if not handlers:
                self._subscriptions.pop(selected_filter, None)
            raise RuntimeError(f"MQTT subscribe failed with result {result}")
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._subscribe_waiters[message_id] = waiter
        try:
            await asyncio.wait_for(waiter, timeout=self.operation_timeout)
        except BaseException:
            handlers.remove(handler)
            if not handlers:
                self._subscriptions.pop(selected_filter, None)
            raise
        finally:
            self._subscribe_waiters.pop(message_id, None)

    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> None:
        client = self._connected_client()
        if qos not in (0, 1, 2):
            raise ValueError("MQTT qos must be 0, 1, or 2")
        info = client.publish(str(topic), bytes(payload), qos=int(qos), retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with result {info.rc}")
        await asyncio.to_thread(
            info.wait_for_publish,
            timeout=self.operation_timeout,
        )
        if not info.is_published():
            raise TimeoutError(f"MQTT publish timed out for topic {topic}")

    def _connected_client(self) -> mqtt.Client:
        if not self.connected or self._client is None:
            raise RuntimeError("MQTT transport is not connected")
        return self._client

    async def _close_client(self) -> None:
        client = self._client
        if client is None:
            self.connected = False
            return
        self._client = None
        self.connected = False
        try:
            client.disconnect()
        finally:
            await asyncio.to_thread(client.loop_stop)
            self._loop = None
            waiter = self._connect_waiter
            self._connect_waiter = None
            if waiter is not None and not waiter.done():
                waiter.cancel()
            for subscribe_waiter in self._subscribe_waiters.values():
                if not subscribe_waiter.done():
                    subscribe_waiter.cancel()
            self._subscribe_waiters.clear()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            self.last_error = f"MQTT connection rejected: {reason_code}"
            self._resolve_connect(RuntimeError(self.last_error))
            return
        self.connected = True
        self.last_error = None
        for topic_filter in tuple(self._subscriptions):
            result, _message_id = client.subscribe(topic_filter, qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                self.last_error = (
                    f"MQTT resubscribe failed for {topic_filter}: {result}"
                )
        self._resolve_connect(None)

    def _on_connect_fail(self, client, userdata) -> None:
        self.connected = False
        self.last_error = f"MQTT connection failed: {self.host}:{self.port}"
        self._resolve_connect(RuntimeError(self.last_error))

    def _on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties,
    ) -> None:
        self.connected = False
        if reason_code.is_failure:
            self.last_error = f"MQTT disconnected unexpectedly: {reason_code}"

    def _on_message(self, client, userdata, message) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._dispatch_message(str(message.topic), bytes(message.payload)),
            loop,
        )
        future.add_done_callback(self._message_completed)

    def _on_subscribe(
        self,
        client,
        userdata,
        message_id,
        reason_codes,
        properties,
    ) -> None:
        error = None
        if any(reason_code.is_failure for reason_code in reason_codes):
            error = RuntimeError(
                f"MQTT subscription rejected for message {message_id}"
            )
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._resolve_subscription,
            int(message_id),
            error,
        )

    async def _dispatch_message(self, topic: str, payload: bytes) -> None:
        for topic_filter, handlers in tuple(self._subscriptions.items()):
            if not _topic_matches(topic_filter, topic):
                continue
            for handler in tuple(handlers):
                await handler(topic, payload)

    def _message_completed(self, future) -> None:
        try:
            future.result()
        except BaseException as exc:
            self.last_error = f"MQTT message handler failed: {exc}"

    def _resolve_connect(self, error: BaseException | None) -> None:
        loop = self._loop
        waiter = self._connect_waiter
        if loop is None or loop.is_closed() or waiter is None:
            return

        def resolve() -> None:
            if waiter.done():
                return
            if error is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(error)

        loop.call_soon_threadsafe(resolve)

    def _resolve_subscription(
        self,
        message_id: int,
        error: BaseException | None,
    ) -> None:
        waiter = self._subscribe_waiters.get(message_id)
        if waiter is None or waiter.done():
            return
        if error is None:
            waiter.set_result(None)
        else:
            waiter.set_exception(error)


def _topic_matches(topic_filter: str, topic: str) -> bool:
    filters = topic_filter.split("/")
    parts = topic.split("/")
    for index, expected in enumerate(filters):
        if expected == "#":
            return True
        if index >= len(parts):
            return False
        if expected != "+" and expected != parts[index]:
            return False
    return len(filters) == len(parts)


def _validate_topic_filter(topic_filter: str) -> str:
    selected = str(topic_filter or "").strip().strip("/")
    if not selected or "\x00" in selected:
        raise ValueError("MQTT topic filter must not be empty")
    return selected
