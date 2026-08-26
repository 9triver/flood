"""Run the water-domain control loop against a real MQTT broker."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain_os import CommandState, PahoMqttTransport
from domains.flood.domain_system import create_flood_domain_system, station_resource_id


DEFAULT_HOST = "test.mosquitto.org"
DEFAULT_CA_URL = "https://test.mosquitto.org/ssl/mosquitto.org.crt"
STATION_ID = "808J1510"


def telemetry(
    *,
    message_id: str,
    sequence: int,
    sampling_interval_seconds: int,
) -> bytes:
    return json.dumps(
        {
            "message_id": message_id,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "quality": "good",
            "metrics": {
                "sampling_interval_seconds": {
                    "value": sampling_interval_seconds,
                    "unit": "s",
                }
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


async def wait_until(label: str, predicate, *, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {label}")
        await asyncio.sleep(0.05)


async def run(args: argparse.Namespace, ca_file: str | None) -> dict:
    run_id = uuid4().hex
    topic_prefix = f"agent-domain-os/smoke/{run_id}/water"
    transport = PahoMqttTransport(
        host=args.host,
        port=args.port,
        tls=not args.plaintext,
        ca_file=ca_file,
        connect_timeout=args.timeout,
        operation_timeout=args.timeout,
    )
    system = create_flood_domain_system(
        mqtt_transport=transport,
        station_ids=(STATION_ID,),
        mqtt_topic_prefix=topic_prefix,
    )
    command_messages: list[dict] = []

    async def capture_command(topic: str, payload: bytes) -> None:
        command_messages.append(json.loads(payload.decode("utf-8")))

    await system.start()
    try:
        telemetry_topic = f"{topic_prefix}/stations/{STATION_ID}/telemetry"
        command_topic = f"{topic_prefix}/stations/{STATION_ID}/commands"
        await transport.subscribe(command_topic, capture_command)
        await transport.publish(
            telemetry_topic,
            telemetry(
                message_id=f"{run_id}-before",
                sequence=1,
                sampling_interval_seconds=60,
            ),
            qos=1,
        )
        resource_id = station_resource_id(STATION_ID)
        await wait_until(
            "initial telemetry projection",
            lambda: (
                system.runtime.projection(resource_id).get(
                    "sampling_interval_seconds"
                ) is not None
            ),
            timeout=args.timeout,
        )

        pending = await system.request_sampling_interval(
            actor_id="agent.mqtt-smoke-test",
            station_id=STATION_ID,
            seconds=10,
            rationale="Validate the public MQTT domain control loop",
            correlation_id=f"mqtt-smoke-{run_id}",
        )
        acknowledged = await system.runtime.approve(
            pending.command_id,
            approver_id="smoke-test-operator",
        )
        await wait_until(
            "command delivery",
            lambda: bool(command_messages),
            timeout=args.timeout,
        )

        await transport.publish(
            telemetry_topic,
            telemetry(
                message_id=f"{run_id}-after",
                sequence=2,
                sampling_interval_seconds=10,
            ),
            qos=1,
        )
        await wait_until(
            "command confirmation",
            lambda: (
                system.runtime.command(pending.command_id).state
                is CommandState.CONFIRMED
            ),
            timeout=args.timeout,
        )
        confirmed = system.runtime.command(pending.command_id)
        return {
            "broker": f"{args.host}:{args.port}",
            "tls": not args.plaintext,
            "topic_prefix": topic_prefix,
            "command_id": confirmed.command_id,
            "command_state": confirmed.state.value,
            "external_id": acknowledged.external_id,
            "received_command_count": len(command_messages),
            "driver_health": dict(system.hydrology_driver.health().details),
        }
    finally:
        await system.stop()


def download_test_ca(target: Path) -> None:
    request = urllib.request.Request(
        DEFAULT_CA_URL,
        headers={"User-Agent": "agent-domain-os-mqtt-check/0.1"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        target.write_bytes(response.read())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the domain control loop against a real MQTT broker.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument(
        "--plaintext",
        action="store_true",
        help="Use unencrypted MQTT; only suitable for non-sensitive smoke data.",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.port is None:
        args.port = 1883 if args.plaintext else 8883
    return args


def main() -> None:
    args = parse_args()
    if args.ca_file is not None:
        result = asyncio.run(run(args, str(args.ca_file)))
    elif not args.plaintext and args.host == DEFAULT_HOST:
        with tempfile.TemporaryDirectory() as directory:
            ca_file = Path(directory) / "mosquitto.org.crt"
            download_test_ca(ca_file)
            result = asyncio.run(run(args, str(ca_file)))
    else:
        result = asyncio.run(run(args, None))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
