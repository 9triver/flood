"""Run the dos kernel control loop against a real MQTT broker.

Mirrors the first-generation check_domain_mqtt.py scenario on the new
kernel: the script itself plays the telemetry station (808J1510) —
publishes telemetry, receives the downlink command frame, applies it and
reports back — while the kernel runs in a background pump thread with a
journaled (JSONL) journal.

Loop under test:
  device telemetry → broker → driver interrupt → pump → namespace commit
  → act() (privileged) → approve → dispatch → broker → device
  → confirming telemetry → fsck committed

Run: uv run python scripts/check_dos_mqtt.py [--plaintext]
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import sys

sys.path.insert(0, ".")

from dos import JsonlSink, Journal, load_journal, recover
from dos.mqtt import PahoSyncMqttTransport
from domains.flood.dos_instance import (
    INTERVAL_PATH,
    LEVEL_PATH,
    STATUS_PATH,
    build_mqtt_kernel,
    spawn_monitor,
)

DEFAULT_HOST = "test.mosquitto.org"
DEFAULT_CA_URL = "https://test.mosquitto.org/ssl/mosquitto.org.crt"
STATION = "808J1510"


def telemetry_frame(run_id: str, sequence: int, *, level_m: float, interval_s: int) -> bytes:
    return json.dumps(
        {
            "message_id": f"{run_id}-{sequence}",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "quality": "good",
            "metrics": {
                "level_m": {"value": level_m, "unit": "m"},
                "sampling_interval_seconds": {"value": interval_s, "unit": "s"},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def wait_until(label: str, predicate, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while not predicate():
        if time.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {label}")
        time.sleep(0.05)


def main() -> int:
    args = parse_args()
    ca_file = resolve_ca(args)

    run_id = uuid4().hex[:8]
    topic_prefix = f"dos/check/{run_id}/water"
    telemetry_topic = f"{topic_prefix}/stations/{STATION}/telemetry"

    transport = PahoSyncMqttTransport(
        host=args.host,
        port=args.port,
        tls=not args.plaintext,
        ca_file=ca_file,
        connect_timeout=args.timeout,
        operation_timeout=args.timeout,
    )

    journal_dir = tempfile.mkdtemp(prefix="dos-mqtt-")
    journal_path = str(Path(journal_dir) / "journal.jsonl")
    kernel = build_mqtt_kernel(
        transport,
        {STATION},
        topic_prefix=topic_prefix,
        journal=Journal(clock=time.time, sink=JsonlSink(journal_path)),
    )

    cap = kernel.grant(
        f"/hydro/shanhu/stations/{STATION}", {"set_sampling_interval"}, "mqtt-check",
        description="采样间隔调整（值班审批）",
    )
    events: list[str] = []
    spawn_monitor(kernel, cap.token, events)

    command_frames: list[dict] = []

    # the script plays the device: capture downlink commands
    def on_command(topic: str, payload: bytes) -> None:
        command_frames.append(json.loads(payload.decode("utf-8")))

    pump = threading.Thread(target=kernel.run, kwargs={"idle_seconds": 0.02}, daemon=True)
    pump.start()

    try:
        print(f"== broker {args.host}:{args.port} tls={not args.plaintext} prefix={topic_prefix}")
        # 订阅设备侧命令主题（设备角色）
        transport.subscribe(f"{topic_prefix}/stations/{STATION}/commands", on_command)

        print("== 1. 感知：正常水位遥测")
        transport.publish(telemetry_topic, telemetry_frame(run_id, 1, level_m=1.8, interval_s=600), qos=1)
        wait_until("initial telemetry", lambda: kernel.namespace.exists(LEVEL_PATH))
        print(f"  level={kernel.read(LEVEL_PATH).value}m  status={kernel.read(STATUS_PATH).value}")

        print("== 2. 判断：越警遥测唤醒监视进程，act 挂起待审批")
        transport.publish(telemetry_topic, telemetry_frame(run_id, 2, level_m=3.5, interval_s=600), qos=1)
        wait_until("monitor act", lambda: any("act->" in e for e in events))
        wait_until("pending approval", lambda: len(kernel.consistency.pending()) == 1)
        pending = kernel.consistency.pending()[0]
        print(f"  events={events}")
        assert pending.state == "awaiting_approval"

        print("== 3. 控制：值班审批，命令经 broker 下发到设备")
        result = kernel.approve(pending.txn_id, approved_by="值班员-陈", decision=True, reason="冒烟加密采样")
        assert result.state == "dispatched"
        wait_until("device received command", lambda: bool(command_frames))
        frame = command_frames[0]
        print(f"  device saw: {frame}")
        assert frame["operation"] == "set_sampling_interval" and frame["seconds"] == 60

        print("== 4. 反馈：设备执行并上报，fsck 提交事务")
        transport.publish(telemetry_topic, telemetry_frame(run_id, 3, level_m=3.6, interval_s=60), qos=1)
        wait_until("txn committed", lambda: kernel.txn(pending.txn_id).state == "committed")
        print(f"  txn={pending.txn_id} committed  sampling_interval={kernel.read(INTERVAL_PATH).value}s")

        print("== 5. 审计：journal 已落盘且可重放恢复")
        kernel.stop()
        pump.join(timeout=5)
        replayed = load_journal(journal_path)
        # mounts/derives are code — re-register them before recovery
        merged = build_mqtt_kernel(
            InMemoryTransportShim(),
            {STATION},
            topic_prefix=topic_prefix,
            journal=replayed,
        )
        stats = recover(merged)
        print(f"  journal records={len(replayed)}  recovered={stats}")
        assert merged.read(INTERVAL_PATH).value == 60
        assert merged.txn(pending.txn_id).state == "committed"
    finally:
        kernel.stop()
        if pump.is_alive():
            pump.join(timeout=5)
        transport.disconnect()

    print(f"\nOK: dos MQTT closed loop verified (journal: {journal_path})")
    return 0


class InMemoryTransportShim:
    """Recovery does not talk to the network; the bus just records."""

    def __init__(self) -> None:
        self.connected = False
        self.published = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, topic_filter, handler) -> None:
        pass

    def publish(self, topic, payload, qos: int = 1) -> None:
        self.published.append((topic, payload))


def download_test_ca(target: Path) -> None:
    request = urllib.request.Request(DEFAULT_CA_URL, headers={"User-Agent": "dos-mqtt-check/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        target.write_bytes(response.read())


def resolve_ca(args) -> str | None:
    if args.ca_file is not None:
        return str(args.ca_file)
    if args.plaintext or args.host != DEFAULT_HOST:
        return None
    directory = tempfile.mkdtemp(prefix="dos-ca-")
    ca_file = Path(directory) / "mosquitto.org.crt"
    download_test_ca(ca_file)
    return str(ca_file)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the dos kernel against a real MQTT broker.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int)
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--plaintext", action="store_true", help="unencrypted MQTT (smoke data only)")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.port is None:
        args.port = 1883 if args.plaintext else 8883
    return args


if __name__ == "__main__":
    raise SystemExit(main())
