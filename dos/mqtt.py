"""MQTT transport and a telemetry-station driver for the dos kernel.

Wire conventions follow the ones already proven by the first-generation
kernel (``domain_os/transports.py`` / ``domains/flood/domain_system.py``):

- uplink   ``{prefix}/stations/{station_id}/telemetry`` — JSON:
  ``{"message_id", "observed_at", "sequence", "quality", "metrics": {name:
  {"value", "unit"}}}``
- downlink ``{prefix}/stations/{station_id}/commands`` — JSON:
  ``{"txn_id", "operation", ...}``

The driver is transport-agnostic and water-free: it maps metric names to
namespace paths ``{base}/stations/{station_id}/{metric}`` and back.  In
dos terms:

- top half: transport callbacks (paho network thread) call
  ``kernel.interrupt(device_id, (topic, payload))`` — cheap, thread-safe.
- bottom half: ``normalize`` parses/validates JSON, drops duplicates
  (QoS 1 is at-least-once) and malformed frames (counted, last error
  kept), and yields metric commits.
- downlink: ``dispatch`` publishes the command frame; ``validate``
  enforces argument bounds before any transaction is opened.
- fsck: a ``set_sampling_interval`` transaction commits only when
  telemetry evidence newer than the dispatch reports the new value.
"""

from __future__ import annotations

import json
import ssl
import threading
import uuid
from collections import deque
from typing import Callable, Iterable, Optional

from .devices import Driver, PendingTxn

MessageHandler = Callable[[str, bytes], None]

MIN_SAMPLING_INTERVAL = 5
MAX_SAMPLING_INTERVAL = 3600


def _parse_observed_at(value) -> Optional[float]:
    """World time of the frame.  Missing -> None (kernel falls back to
    system time); present but malformed -> caller drops the frame."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def topic_matches(topic_filter: str, topic: str) -> bool:
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


class InMemoryMqttBus:
    """Deterministic MQTT-shaped bus for tests — no broker, no threads."""

    def __init__(self) -> None:
        self.connected = False
        self.published: list[tuple[str, bytes]] = []
        self._subscriptions: list[tuple[str, MessageHandler]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False
        self._subscriptions.clear()

    def subscribe(self, topic_filter: str, handler: MessageHandler) -> None:
        self._subscriptions.append((topic_filter, handler))

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        if not self.connected:
            raise RuntimeError("MQTT bus is not connected")
        self.published.append((topic, bytes(payload)))
        self.inject(topic, payload)

    def inject(self, topic: str, payload: bytes) -> None:
        """Deliver an uplink frame as if it came from the device."""
        for topic_filter, handler in tuple(self._subscriptions):
            if topic_matches(topic_filter, topic):
                handler(topic, bytes(payload))


class PahoSyncMqttTransport:
    """Synchronous MQTT transport backed by Eclipse Paho.

    Paho's network thread runs callbacks; handlers must be non-blocking
    (the driver's handler only queues a kernel interrupt).  TLS, QoS 1
    publishing and automatic resubscribe-on-reconnect included.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 8883,
        tls: bool = True,
        ca_file: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        keepalive: int = 60,
        connect_timeout: float = 15.0,
        operation_timeout: float = 10.0,
    ) -> None:
        if not str(host or "").strip():
            raise ValueError("MQTT host must not be empty")
        self.host = host
        self.port = int(port)
        self.tls = bool(tls)
        self.ca_file = ca_file
        self.username = username
        self.password = password
        self.client_id = client_id or f"dos-{uuid.uuid4().hex[:12]}"
        self.keepalive = int(keepalive)
        self.connect_timeout = float(connect_timeout)
        self.operation_timeout = float(operation_timeout)
        self.connected = False
        self.last_error: Optional[str] = None
        self._handlers: list[tuple[str, MessageHandler]] = []
        self._client = None
        self._connect_event = threading.Event()

    def connect(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        if self.username is not None:
            client.username_pw_set(self.username, self.password)
        if self.tls:
            client.tls_set(ca_certs=self.ca_file, cert_reqs=ssl.CERT_REQUIRED)
        self._client = client
        self._connect_event.clear()
        client.connect(self.host, port=self.port, keepalive=self.keepalive)
        client.loop_start()
        if not self._connect_event.wait(self.connect_timeout):
            self.disconnect()
            raise TimeoutError(f"MQTT connect timed out: {self.host}:{self.port}")

    def disconnect(self) -> None:
        client, self._client = self._client, None
        self.connected = False
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()

    def subscribe(self, topic_filter: str, handler: MessageHandler) -> None:
        if not self.connected or self._client is None:
            raise RuntimeError("MQTT transport is not connected")
        self._handlers.append((topic_filter, handler))
        result, _ = self._client.subscribe(topic_filter, qos=1)
        if result != 0:
            self._handlers.remove((topic_filter, handler))
            raise RuntimeError(f"MQTT subscribe failed with result {result}")

    def publish(self, topic: str, payload: bytes, qos: int = 1) -> None:
        if not self.connected or self._client is None:
            raise RuntimeError("MQTT transport is not connected")
        info = self._client.publish(str(topic), bytes(payload), qos=int(qos), retain=False)
        if info.rc != 0:
            raise RuntimeError(f"MQTT publish failed with result {info.rc}")
        info.wait_for_publish(timeout=self.operation_timeout)
        if not info.is_published():
            raise TimeoutError(f"MQTT publish timed out for topic {topic}")

    # ------------------------------------------------------ paho callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if getattr(reason_code, "is_failure", False):
            self.last_error = f"MQTT connection rejected: {reason_code}"
            self._connect_event.set()
            return
        self.connected = True
        self.last_error = None
        self._connect_event.set()
        for topic_filter, _ in tuple(self._handlers):  # resubscribe on reconnect
            client.subscribe(topic_filter, qos=1)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected = False
        if getattr(reason_code, "is_failure", False):
            self.last_error = f"MQTT disconnected unexpectedly: {reason_code}"

    def _on_message(self, client, userdata, message) -> None:
        topic = str(message.topic)
        payload = bytes(message.payload)
        for topic_filter, handler in tuple(self._handlers):
            if topic_matches(topic_filter, topic):
                handler(topic, payload)  # must be non-blocking


class MqttTelemetryDriver(Driver):
    """Telemetry-station driver over an MQTT transport.

    Mount with ``base`` equal to the mount prefix, e.g. mount("/hydro/shanhu",
    MqttTelemetryDriver(transport, base="/hydro/shanhu", station_ids={"808J1510"})).
    """

    privileged_actions = frozenset({"set_sampling_interval"})
    default_txn_timeout = 30.0

    def __init__(
        self,
        transport,
        station_ids: Iterable[str],
        *,
        base: str,
        topic_prefix: str = "water",
    ) -> None:
        prefix = str(topic_prefix or "").strip().strip("/")
        if not prefix or "+" in prefix or "#" in prefix:
            raise ValueError("topic prefix must be a concrete non-empty topic")
        self.device_id = f"mqtt:{prefix}"
        self.transport = transport
        self.station_ids = frozenset(station_ids)
        self.topic_prefix = prefix
        self.base = "/" + base.strip("/")
        # interrupt statistics — interrupts are unreliable; we count what we drop
        self.received = 0
        self.dropped_malformed = 0
        self.dropped_duplicates = 0
        self.last_error: Optional[str] = None
        self._seen_message_ids: deque = deque(maxlen=1024)
        self._seen_set: set = set()

    def attach(self, kernel) -> None:
        super().attach(kernel)
        self.transport.connect()
        self.transport.subscribe(
            f"{self.topic_prefix}/stations/+/telemetry",
            lambda topic, payload: kernel.interrupt(self.device_id, (topic, payload)),
        )

    # ------------------------------------------------------------ top half

    def normalize(self, raw: object) -> Iterable[tuple]:
        self.received += 1
        topic, payload = raw
        station = self._station_from_topic(topic)
        if station is None or station not in self.station_ids:
            self.dropped_malformed += 1
            self.last_error = f"unknown station in topic: {topic}"
            return
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.dropped_malformed += 1
            self.last_error = f"malformed telemetry JSON: {exc}"
            return
        if not isinstance(message, dict) or not isinstance(message.get("metrics"), dict) or not message["metrics"]:
            self.dropped_malformed += 1
            self.last_error = "telemetry must carry a non-empty metrics object"
            return
        observed_at = _parse_observed_at(message.get("observed_at"))
        if observed_at is None and message.get("observed_at") is not None:
            self.dropped_malformed += 1
            self.last_error = "telemetry observed_at must be ISO-8601 with timezone"
            return
        message_id = str(message.get("message_id") or "")
        if message_id:
            if message_id in self._seen_set:
                self.dropped_duplicates += 1
                return
            if len(self._seen_message_ids) == self._seen_message_ids.maxlen:
                self._seen_set.discard(self._seen_message_ids[0])
            self._seen_message_ids.append(message_id)
            self._seen_set.add(message_id)
        for metric, entry in message["metrics"].items():
            value = entry["value"] if isinstance(entry, dict) else entry
            if observed_at is not None:
                yield self.metric_path(station, str(metric)), value, observed_at
            else:
                yield self.metric_path(station, str(metric)), value

    # -------------------------------------------------------------- downlink

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        if action != "set_sampling_interval":
            return f"unsupported action: {action}"
        seconds = args.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return "sampling interval seconds must be an integer"
        if not (MIN_SAMPLING_INTERVAL <= seconds <= MAX_SAMPLING_INTERVAL):
            return f"sampling interval seconds must be between {MIN_SAMPLING_INTERVAL} and {MAX_SAMPLING_INTERVAL}"
        return None

    def dispatch(self, txn: PendingTxn) -> None:
        station = self._station_from_path(txn.path)
        payload = json.dumps(
            {
                "txn_id": txn.txn_id,
                "operation": txn.action,
                "seconds": txn.args["seconds"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.transport.publish(f"{self.topic_prefix}/stations/{station}/commands", payload, qos=1)

    # ----------------------------------------------------------- fsck rule

    def verify(self, txn: PendingTxn, read) -> str:
        station = self._station_from_path(txn.path)
        if station is None:
            return "failed"
        snap = read(self.metric_path(station, "sampling_interval_seconds"))
        if snap is None:
            return "pending"
        return "committed" if snap.value == txn.args["seconds"] else "pending"

    # -------------------------------------------------------------- helpers

    def metric_path(self, station: str, metric: str) -> str:
        return f"{self.base}/stations/{station}/{metric}"

    def _station_from_path(self, path: str) -> Optional[str]:
        parts = path.strip("/").split("/")
        stations_index = parts.index("stations") if "stations" in parts else -1
        if stations_index < 0 or stations_index + 2 > len(parts) - 1:
            return None
        return parts[stations_index + 1]

    def _station_from_topic(self, topic: str) -> Optional[str]:
        parts = topic.split("/")
        if len(parts) < 3 or parts[-1] != "telemetry":
            return None
        return parts[-2]
