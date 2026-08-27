from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)

from .paths import STATIONS_BASE, station_metric_path, station_path


BOUNDARY_STATIONS = {
    "upstream": "坝址边界站",
    "interval1": "区间1边界站",
    "interval2": "区间2边界站",
    "tonggu": "同古河边界站",
}


class MqttHydrologyDriver(Driver):
    """Normalize MQTT hydrology frames into station resources and metrics."""

    device_id = "flood:mqtt-hydrology"
    operation_timeout_seconds = None

    def __init__(self, *, topic_prefix: str = "water"):
        self.topic_prefix = topic_prefix.strip("/")

    @property
    def topic_filter(self) -> str:
        return f"{self.topic_prefix}/stations/+/telemetry"

    def bootstrap(self, stations: dict[str, str] | None = None) -> None:
        self.kernel.interrupt(
            self.device_id,
            {
                "kind": "register_stations",
                "stations": dict(stations or BOUNDARY_STATIONS),
                "observed_at": self.kernel.clock(),
            },
        )

    def ingest(self, topic: str, payload: bytes | str | dict) -> None:
        self.kernel.interrupt(
            self.device_id,
            {"kind": "mqtt", "topic": topic, "payload": payload},
        )

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict):
            raise ValueError("MQTT ingress frame must be an object")
        if raw.get("kind") == "register_stations":
            observed_at = float(raw["observed_at"])
            refs = []
            for station_id, name in sorted((raw.get("stations") or {}).items()):
                reference = station_path(str(station_id))
                refs.append(reference)
                yield NormalizedObservation(
                    reference,
                    {
                        "kind": "sensor",
                        "sensor_type": "hydrological_boundary_station",
                        "station_id": str(station_id),
                        "name": str(name),
                        "protocol": "mqtt",
                        "topic": (
                            f"{self.topic_prefix}/stations/{station_id}/telemetry"
                        ),
                        "metrics": ["flow_m3s"],
                    },
                    observed_at,
                    self.device_id,
                )
            yield NormalizedObservation(
                STATIONS_BASE,
                {
                    "kind": "sensor_catalog",
                    "protocol": "mqtt",
                    "topic_filter": self.topic_filter,
                    "count": len(refs),
                    "refs": refs,
                },
                observed_at,
                self.device_id,
            )
            return
        if raw.get("kind") != "mqtt":
            raise ValueError("unsupported hydrology ingress frame")

        topic = str(raw.get("topic") or "")
        station_id = self._station_from_topic(topic)
        frame = _decode_payload(raw.get("payload"))
        payload_station_id = str(frame.get("station_id") or "")
        if payload_station_id and payload_station_id != station_id:
            raise ValueError(
                f"payload station_id {payload_station_id!r} does not match topic"
            )
        observed_at = _timestamp(frame.get("observed_at"))
        message_id = str(frame.get("message_id") or "")
        metrics = frame.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError("MQTT frame requires a non-empty metrics object")
        for metric, raw_metric in metrics.items():
            if isinstance(raw_metric, dict):
                value = raw_metric.get("value")
                unit = raw_metric.get("unit")
            else:
                value = raw_metric
                unit = None
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"metric {metric} must contain a numeric value")
            yield NormalizedObservation(
                station_metric_path(station_id, str(metric)),
                {
                    "value": float(value),
                    "unit": unit,
                    "station_id": station_id,
                    "metric": str(metric),
                    "message_id": message_id,
                    "topic": topic,
                },
                observed_at,
                self.device_id,
            )

    def _station_from_topic(self, topic: str) -> str:
        parts = topic.strip("/").split("/")
        expected_prefix = self.topic_prefix.split("/")
        if (
            parts[: len(expected_prefix)] != expected_prefix
            or len(parts) != len(expected_prefix) + 3
            or parts[-3] != "stations"
            or parts[-1] != "telemetry"
        ):
            raise ValueError(
                f"topic {topic!r} does not match {self.topic_filter!r}"
            )
        return parts[-2]

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        return "hydrology telemetry resources are read-only in this version"

    def dispatch(self, operation: Operation) -> None:
        raise RuntimeError("hydrology telemetry resources are read-only")

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return Verification.pending()


class PahoMqttIngress:
    """Optional real MQTT transport. Network lifecycle stays outside Kernel."""

    def __init__(
        self,
        driver: MqttHydrologyDriver,
        *,
        host: str,
        port: int = 1883,
        client_id: str = "domain-os-mvp-flood",
    ):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - optional environment
            raise RuntimeError("paho-mqtt is required for real MQTT ingress") from exc
        self.driver = driver
        self.host = host
        self.port = int(port)
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def start(self) -> None:
        self.client.connect(self.host, self.port)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        del userdata, flags, reason_code, properties
        client.subscribe(self.driver.topic_filter, qos=1)

    def _on_message(self, client, userdata, message) -> None:
        del client, userdata
        self.driver.ingest(message.topic, message.payload)


def mqtt_payload(
    station_id: str,
    *,
    observed_at: float | str,
    flow_m3s: float,
    message_id: str,
) -> dict[str, Any]:
    return {
        "station_id": station_id,
        "message_id": message_id,
        "observed_at": observed_at,
        "metrics": {
            "flow_m3s": {"value": float(flow_m3s), "unit": "m3/s"},
        },
    }


def _decode_payload(payload: object) -> dict:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("MQTT payload must be JSON object data")
    return payload


def _timestamp(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("MQTT payload requires observed_at")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
