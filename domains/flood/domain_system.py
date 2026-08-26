"""Water-domain package for the agent-oriented domain OS prototype."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from domain_os import (
    Capability,
    CapabilityRisk,
    Command,
    CommandResult,
    DomainPolicy,
    DomainRuntime,
    DomainStore,
    DriverHealth,
    InfrastructureDriver,
    Intent,
    MqttTransport,
    Observation,
    ObservationQuality,
    ObservationSink,
    Resource,
    RiskBasedPolicy,
    new_id,
    utc_now,
)


DOMAIN_ID = "water.flood"
STATION_RESOURCE_TYPE = "water.hydrology-station"
SET_SAMPLING_INTERVAL = "water.station.set-sampling-interval"
MQTT_DRIVER_ID = "water.infrastructure.mqtt-hydrology"
STATIONS_PATH = Path(__file__).resolve().parent / "data" / "objects" / "station.jsonl"


def station_resource_id(station_id: str) -> str:
    return f"water.station/{str(station_id).strip()}"


class MqttHydrologyDriver(InfrastructureDriver):
    """Maps MQTT telemetry and commands to stable water-domain contracts."""

    driver_id = MQTT_DRIVER_ID

    def __init__(
        self,
        transport: MqttTransport,
        station_ids: set[str],
        *,
        topic_prefix: str = "water",
    ) -> None:
        if not isinstance(transport, MqttTransport):
            raise TypeError("transport must implement MqttTransport")
        self.transport = transport
        self.station_ids = frozenset(station_ids)
        self.topic_prefix = _topic_prefix(topic_prefix)
        self.telemetry_filter = f"{self.topic_prefix}/stations/+/telemetry"
        self._sink: ObservationSink | None = None

    async def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        await self.transport.connect()
        await self.transport.subscribe(self.telemetry_filter, self._on_message)

    async def stop(self) -> None:
        self._sink = None
        await self.transport.disconnect()

    def health(self) -> DriverHealth:
        last_error = getattr(self.transport, "last_error", None)
        return DriverHealth(
            driver_id=self.driver_id,
            connected=bool(self.transport.connected),
            checked_at=utc_now(),
            details={
                "station_count": len(self.station_ids),
                "topic_prefix": self.topic_prefix,
                "last_error": last_error,
            },
        )

    async def execute(self, command: Command) -> CommandResult:
        station_id = command.intent.resource_id.removeprefix("water.station/")
        if station_id not in self.station_ids:
            return CommandResult(
                accepted=False,
                error=f"station is not managed by MQTT driver: {station_id}",
            )
        if command.intent.capability_id != SET_SAMPLING_INTERVAL:
            return CommandResult(
                accepted=False,
                error=f"unsupported capability: {command.intent.capability_id}",
            )
        seconds = command.intent.arguments.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return CommandResult(
                accepted=False,
                error="sampling interval seconds must be an integer",
            )
        if seconds < 5 or seconds > 3600:
            return CommandResult(
                accepted=False,
                error="sampling interval seconds must be between 5 and 3600",
            )

        external_id = f"mqtt-{command.command_id}"
        payload = json.dumps(
            {
                "command_id": command.command_id,
                "external_id": external_id,
                "operation": "set_sampling_interval",
                "seconds": seconds,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await self.transport.publish(
            f"{self.topic_prefix}/stations/{station_id}/commands",
            payload,
            qos=1,
        )
        return CommandResult(
            accepted=True,
            external_id=external_id,
            output={"transport": "mqtt", "qos": 1},
            expected_state={"sampling_interval_seconds": seconds},
        )

    async def _on_message(self, topic: str, payload: bytes) -> None:
        sink = self._sink
        if sink is None:
            return
        station_id = self._station_id_from_topic(topic)
        if station_id not in self.station_ids:
            raise ValueError(f"unknown station in MQTT topic: {station_id}")
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid hydrology telemetry JSON") from exc
        if not isinstance(message, dict):
            raise ValueError("hydrology telemetry must be a JSON object")

        observed_at = _parse_timestamp(message.get("observed_at"))
        quality = _quality(message.get("quality", ObservationQuality.GOOD.value))
        sequence = message.get("sequence")
        if sequence is not None:
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("telemetry sequence must be an integer")
        metrics = message.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError("hydrology telemetry metrics must be a non-empty object")
        message_id = str(message.get("message_id") or new_id("mqtt-message"))

        for metric, raw_value in metrics.items():
            if isinstance(raw_value, dict):
                if "value" not in raw_value:
                    raise ValueError(f"telemetry metric has no value: {metric}")
                value = raw_value["value"]
                unit = raw_value.get("unit")
            else:
                value = raw_value
                unit = None
            await sink(
                Observation(
                    observation_id=f"{message_id}:{metric}",
                    resource_id=station_resource_id(station_id),
                    metric=str(metric),
                    value=value,
                    unit=str(unit) if unit is not None else None,
                    observed_at=observed_at,
                    received_at=utc_now(),
                    quality=quality,
                    sequence=sequence,
                    source_ref=topic,
                    attributes={"transport": "mqtt"},
                )
            )

    def _station_id_from_topic(self, topic: str) -> str:
        parts = topic.split("/")
        prefix = self.topic_prefix.split("/")
        if (
            len(parts) != len(prefix) + 3
            or parts[:len(prefix)] != prefix
            or parts[len(prefix)] != "stations"
            or parts[-1] != "telemetry"
        ):
            raise ValueError(f"invalid hydrology telemetry topic: {topic}")
        return parts[-2]


@dataclass(frozen=True, slots=True)
class FloodDomainSystem:
    runtime: DomainRuntime
    hydrology_driver: MqttHydrologyDriver

    async def start(self) -> None:
        await self.runtime.start()

    async def stop(self) -> None:
        await self.runtime.stop()

    async def request_sampling_interval(
        self,
        *,
        actor_id: str,
        station_id: str,
        seconds: int,
        rationale: str,
        correlation_id: str | None = None,
    ) -> Command:
        return await self.runtime.submit_intent(
            Intent(
                intent_id=new_id("intent"),
                actor_id=actor_id,
                resource_id=station_resource_id(station_id),
                capability_id=SET_SAMPLING_INTERVAL,
                arguments={"seconds": seconds},
                requested_at=utc_now(),
                rationale=rationale,
                correlation_id=correlation_id,
            )
        )


def create_flood_domain_system(
    *,
    mqtt_transport: MqttTransport,
    station_ids: tuple[str, ...] | None = None,
    policy: DomainPolicy | None = None,
    store: DomainStore | None = None,
    mqtt_topic_prefix: str = "water",
) -> FloodDomainSystem:
    rows = _load_station_rows()
    selected = set(station_ids) if station_ids is not None else set(rows)
    missing = selected - rows.keys()
    if missing:
        raise ValueError(f"unknown water stations: {', '.join(sorted(missing))}")

    runtime = DomainRuntime(
        domain_id=DOMAIN_ID,
        policy=policy or RiskBasedPolicy(),
        store=store,
    )
    driver = MqttHydrologyDriver(
        mqtt_transport,
        selected,
        topic_prefix=mqtt_topic_prefix,
    )
    runtime.register_driver(driver)
    runtime.register_capability(
        Capability(
            capability_id=SET_SAMPLING_INTERVAL,
            description="Set the telemetry sampling interval of a hydrology station",
            risk=CapabilityRisk.CONTROLLED,
            idempotent=True,
        )
    )
    for station_id in sorted(selected):
        row = rows[station_id]
        runtime.register_resource(
            Resource(
                resource_id=station_resource_id(station_id),
                resource_type=STATION_RESOURCE_TYPE,
                name=str(row.get("name") or station_id),
                driver_id=driver.driver_id,
                capabilities=frozenset({SET_SAMPLING_INTERVAL}),
                attributes={
                    "station_id": station_id,
                    "station_type": row.get("station_type"),
                    "source_system": row.get("source_system"),
                    "river_id": row.get("river_id"),
                    "longitude": row.get("longitude"),
                    "latitude": row.get("latitude"),
                    "observation_items": row.get("observation_items"),
                },
            )
        )
    return FloodDomainSystem(runtime=runtime, hydrology_driver=driver)


def _load_station_rows() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in STATIONS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        station_id = str(row.get("station_id") or "").strip()
        if station_id:
            rows[station_id] = row
    return rows


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("telemetry observed_at is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("telemetry observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("telemetry observed_at must include a timezone")
    return parsed


def _quality(value: Any) -> ObservationQuality:
    try:
        return ObservationQuality(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported telemetry quality: {value}") from exc


def _topic_prefix(value: str) -> str:
    prefix = str(value or "").strip().strip("/")
    if not prefix or "\x00" in prefix or "+" in prefix or "#" in prefix:
        raise ValueError("MQTT topic prefix must be a non-empty concrete topic")
    if any(not part for part in prefix.split("/")):
        raise ValueError("MQTT topic prefix must not contain empty levels")
    return prefix
