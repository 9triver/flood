"""Flood forecast / impact closed loop on the dos kernel.

Default run uses an instant fake model (no torch, no weights) and walks
the full chain: boundary telemetry over the MQTT-shaped bus → mirror →
stateless trigger → compute device transaction → committed forecast →
automatic impact sweep.

    uv run python scripts/check_dos_forecast.py            # fake model
    uv run python scripts/check_dos_forecast.py --real     # real CNN_V2 (slow)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from dos import Kernel
from dos.mqtt import InMemoryMqttBus
from domains.flood.dos_forecast import (
    BOUNDARIES,
    FORECAST_MOUNT,
    LATEST_PATH as FORECAST_LATEST,
    mount_forecast,
    spawn_forecast_trigger,
)
from domains.flood.dos_impact import IMPACT_LATEST, IMPACT_MOUNT, mount_impact, spawn_impact_auto
from domains.flood.dos_instance import build_mqtt_kernel

PREFIX = "water"
BASE_TS = 1_800_000_000.0  # arbitrary world-time epoch for the demo


def banner(text: str) -> None:
    print(f"\n== {text} ==")


class BoundaryFeeder:
    """Plays four boundary hydrology stations on the bus (mock telemetry)."""

    def __init__(self, bus: InMemoryMqttBus):
        self.bus = bus
        self.sent = 0

    def feed(self, hour: float, flows: dict[str, float]) -> None:
        for boundary, flow in flows.items():
            observed_at = datetime.fromtimestamp(BASE_TS + hour * 3600.0, tz=timezone.utc).isoformat()
            frame = json.dumps(
                {
                    "message_id": f"h{hour}-{boundary}-{self.sent}",
                    "observed_at": observed_at,
                    "metrics": {"flow_m3s": {"value": flow, "unit": "m3/s"}},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self.bus.inject(f"{PREFIX}/stations/{boundary}/telemetry", frame)
        self.sent += 1


def fake_forecast_runner(args: dict, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    (target / "depth_series.npy").write_bytes(b"fake")
    return {
        "stats": {"wet_cells": 1234, "max_depth_m": 2.75},
        "artifacts": {"depth_series": str(target / "depth_series.npy"), "max_depth_csv": str(target / "max_depth.csv")},
        "model": {"model_name": "FAKE"},
    }


def fake_impact_runner(args: dict, target: Path, forecast_meta: dict) -> dict:
    return {
        "summary": {"affected": 2, "by_risk": {"high": 1, "medium": 1}},
        "highlights": [{"id": "bridge-1", "risk": "high"}],
        "artifacts": {"geojson": str(target / "impact.geojson")},
    }


def real_forecast_runner(args: dict, target: Path) -> dict:
    """Adapter retained for the check script — the canonical implementation
    lives in domains.flood.dos_forecast.real_cnn_runner."""
    from domains.flood.dos_forecast import real_cnn_runner

    return real_cnn_runner(args, target)


def wait_until(predicate, label: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while not predicate():
        if time.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {label}")
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="run the real CNN_V2 model (slow, needs torch + weights)")
    args = parser.parse_args()

    forecast_runner = real_forecast_runner if args.real else fake_forecast_runner
    timeout = 1200.0 if args.real else 60.0

    bus = InMemoryMqttBus()
    kernel = build_mqtt_kernel(bus, set(BOUNDARIES), topic_prefix=PREFIX)
    mount_forecast(kernel, forecast_runner)
    mount_impact(kernel, fake_impact_runner)
    forecast_cap = kernel.grant(FORECAST_MOUNT, {"run_forecast"}, "check-boot")
    impact_cap = kernel.grant(IMPACT_MOUNT, {"analyze_impact"}, "check-boot")
    trigger_events: list[str] = []
    impact_events: list[str] = []
    spawn_forecast_trigger(kernel, forecast_cap.token, sink=trigger_events)
    spawn_impact_auto(kernel, impact_cap.token, targets=[{"type": "bridge", "id": "bridge-1"}], sink=impact_events)

    pump = threading.Thread(target=kernel.run, kwargs={"idle_seconds": 0.02}, daemon=True)
    pump.start()
    feeder = BoundaryFeeder(bus)

    try:
        banner("1. 感知：四边界水文站遥测（24 小时窗口，低于阈值）")
        for hour in range(24):
            feeder.feed(hour, {b: 10.0 for b in BOUNDARIES})
            time.sleep(0.01)
        wait_until(lambda: len(kernel.mirror.query(f"/hydro/shanhu/stations/interval1/flow_m3s")) >= 24, "mirror window", timeout)
        mirror_stats = kernel.mirror.stats()
        print(f"  mirror: {mirror_stats['paths']} paths / {mirror_stats['samples']} samples (world-time indexed)")
        print(f"  trigger events: {trigger_events}")
        assert trigger_events == []

        banner("2. 判断：洪峰到达，窗口总量越阈 → 无状态触发进程发起预测")
        feeder.feed(24, {b: 60.0 for b in BOUNDARIES})  # total 240 > 230
        wait_until(lambda: kernel.try_read(FORECAST_LATEST) is not None, "forecast committed", timeout)
        latest = kernel.read(FORECAST_LATEST).value
        meta = kernel.read(f"{FORECAST_MOUNT}/{latest['id']}").value
        print(f"  trigger events: {trigger_events}")
        print(f"  forecast {latest['id']} committed")
        print(f"  valid_from={datetime.fromtimestamp(meta['valid_from'], tz=timezone.utc).isoformat()}")
        print(f"  input: total={meta['input']['total_m3s']} m3/s window={meta['input']['window_hours']}h")
        print(f"  stats: {meta['stats']}")
        print(f"  artifacts: {list(meta['artifacts'])} (大数据在盘上，命名空间只存句柄)")

        banner("3. 研判：自动影响评估进程跟进")
        wait_until(lambda: kernel.try_read(IMPACT_LATEST) is not None, "impact sweep", timeout)
        impact = kernel.read(IMPACT_LATEST).value
        print(f"  impact events: {impact_events}")
        print(f"  impact {impact['id']} for forecast {impact['forecast_id']}: {kernel.read(f'{IMPACT_MOUNT}/{impact['id']}').value['summary']}")

        banner("4. 审计：journal 记录了预测的全部输入")
        open_records = [r for r in kernel.journal.replay() if r.kind == "txn" and r.payload.get("event") == "open"]
        forecast_open = [r for r in open_records if r.payload.get("action") == "run_forecast"]
        self_check = len(forecast_open[0].payload["args"]["stations"]["interval1"])
        print(f"  run_forecast open 记账 {len(forecast_open)} 条；interval1 输入快照 {self_check} 点（完整可回放）")
        assert self_check == 25  # hours 0..24 inclusive — the legacy 24h window point count
    finally:
        kernel.stop()
        pump.join(timeout=5)

    print("\nOK: dos 洪水预测/影响闭环验证通过（遥测→镜像→无状态触发→计算设备事务→自动研判）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
