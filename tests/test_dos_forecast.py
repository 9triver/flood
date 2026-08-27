"""Flood forecast closed loop on the dos kernel (fake model runner)."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from dos import Kernel
from dos.mqtt import InMemoryMqttBus
from domains.flood.dos_forecast import (
    BOUNDARIES,
    FORECAST_MOUNT,
    LATEST_PATH,
    PENDING_PATH,
    RUN_FORECAST,
    boundary_flow_path,
    mount_forecast,
    spawn_forecast_trigger,
)
from domains.flood.dos_instance import build_mqtt_kernel

PREFIX = "water"


def telemetry_frame(message_id: str, boundary: str, observed_at: float, flow: float) -> bytes:
    from datetime import datetime, timezone

    return json.dumps(
        {
            "message_id": message_id,
            "observed_at": datetime.fromtimestamp(observed_at, tz=timezone.utc).isoformat(),
            "metrics": {"flow_m3s": {"value": flow, "unit": "m3/s"}},
        },
        separators=(",", ":"),
    ).encode("utf-8")


class FakeRunner:
    """Instant model: records inputs, can be told to fail."""

    def __init__(self, error: str | None = None):
        self.error = error
        self.calls: list[tuple[dict, Path]] = []

    def __call__(self, args: dict, target: Path) -> dict:
        self.calls.append((args, target))
        if self.error:
            raise RuntimeError(self.error)
        target.mkdir(parents=True, exist_ok=True)
        (target / "depth_series.npy").write_bytes(b"fake")
        return {
            "stats": {"wet_cells": 1234, "max_depth_m": 2.75},
            "artifacts": {
                "depth_series": str(target / "depth_series.npy"),
                "max_depth_csv": str(target / "max_depth.csv"),
            },
        }


def build(runner, *, clock=None):
    bus = InMemoryMqttBus()
    kernel = build_mqtt_kernel(bus, set(BOUNDARIES), topic_prefix=PREFIX, clock=clock)
    mount_forecast(kernel, runner, artifact_root=Path("/tmp/dos-forecast-test") / str(time.time()))
    cap = kernel.grant(FORECAST_MOUNT, {RUN_FORECAST}, "forecast-test")
    events: list[str] = []
    spawn_forecast_trigger(kernel, cap.token, sink=events)
    return kernel, bus, cap, events, runner


def feed(bus, hour: float, flows: dict[str, float]):
    for boundary, flow in flows.items():
        ts = 1_800_000_000.0 + hour * 3600.0
        bus.inject(
            f"{PREFIX}/stations/{boundary}/telemetry",
            telemetry_frame(f"h{hour}-{boundary}", boundary, ts, flow),
        )


def settle(kernel, predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() >= deadline:
            raise TimeoutError("condition not reached")
        kernel.pump()
        time.sleep(0.01)
    return kernel.pump()


class TestForecastLoop(unittest.TestCase):
    def test_trigger_run_commit_chain(self):
        runner = FakeRunner()
        kernel, bus, cap, events, runner = build(runner)
        # 25 hourly points: below threshold, then above
        for hour in range(24):
            feed(bus, hour, {b: 10.0 for b in BOUNDARIES})
        settle(kernel, lambda: len(kernel.mirror.query(boundary_flow_path("interval1"))) == 24)
        self.assertEqual(events, [])  # below threshold: no trigger
        self.assertEqual(runner.calls, [])

        feed(bus, 24, {b: 60.0 for b in BOUNDARIES})  # total 240 > 230
        settle(kernel, lambda: kernel.try_read(LATEST_PATH) is not None)

        txn = kernel.consistency.pending()
        self.assertEqual(txn, [])  # terminal already
        latest = kernel.read(LATEST_PATH).value
        self.assertTrue(latest["id"].startswith("fcst_"))
        meta = kernel.read(f"{FORECAST_MOUNT}/{latest['id']}").value
        self.assertEqual(meta["stats"]["wet_cells"], 1234)
        self.assertEqual(meta["input"]["total_m3s"], 240.0)
        self.assertEqual(meta["valid_from"], 1_800_000_000.0 + 24 * 3600.0)
        self.assertEqual(kernel.read(PENDING_PATH).value, {})
        # audit: the input snapshot is in the journal
        txn_records = [r for r in kernel.journal.replay() if r.kind == "txn" and r.payload.get("event") == "open"]
        self.assertEqual(len(txn_records), 1)
        self.assertIn("interval1", txn_records[0].payload["args"]["stations"])

    def test_no_retrigger_without_new_data(self):
        runner = FakeRunner()
        kernel, bus, cap, events, runner = build(runner)
        for hour in range(25):
            feed(bus, hour, {b: 60.0 if hour >= 24 else 10.0 for b in BOUNDARIES})
        settle(kernel, lambda: kernel.try_read(LATEST_PATH) is not None)
        first_id = kernel.read(LATEST_PATH).value["id"]
        self.assertEqual(len(runner.calls), 1)

        # duplicate frame at the same world time: covered by the committed
        # forecast's input — no re-run
        feed(bus, 24, {b: 61.0 for b in BOUNDARIES})
        kernel.pump()
        self.assertEqual(len(runner.calls), 1)

        # genuinely newer observations with a still-high total: re-run
        feed(bus, 25, {b: 62.0 for b in BOUNDARIES})
        settle(kernel, lambda: len(runner.calls) == 2 and kernel.read(PENDING_PATH).value == {})
        self.assertNotEqual(kernel.read(LATEST_PATH).value["id"], first_id)

    @staticmethod
    def _any_failed(kernel) -> bool:
        return any(t.state == "failed" for t in list(kernel.consistency._pending.values()))

    def test_model_failure_is_explicit(self):
        runner = FakeRunner(error="model exploded")
        kernel, bus, cap, events, runner = build(runner)
        for hour in range(25):
            feed(bus, hour, {b: 60.0 if hour >= 24 else 10.0 for b in BOUNDARIES})
        settle(kernel, lambda: self._any_failed(kernel))
        failed = [t for t in list(kernel.consistency._pending.values()) if t.state == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("model exploded", failed[0].error)
        self.assertIsNone(kernel.try_read(LATEST_PATH))  # no half forecast
        self.assertEqual(kernel.read(PENDING_PATH).value, {})

    def test_invalid_args_rejected_before_journaling(self):
        runner = FakeRunner()
        kernel, bus, cap, events, runner = build(runner)
        from dos import InvalidActionError

        with self.assertRaises(InvalidActionError):
            kernel.act(cap.token, LATEST_PATH, RUN_FORECAST, {"stations": {}})
        self.assertEqual([r for r in kernel.journal.replay() if r.kind == "txn"], [])

    def test_unknown_forecast_input_version(self):
        """Evidence freshness on a compute device: a pre-existing job result
        must not commit a transaction dispatched after it."""
        runner = FakeRunner()
        kernel, bus, cap, events, runner = build(runner)
        # produce one successful forecast the slow way
        for hour in range(25):
            feed(bus, hour, {b: 60.0 if hour >= 24 else 10.0 for b in BOUNDARIES})
        settle(kernel, lambda: kernel.try_read(LATEST_PATH) is not None)
        first_latest = kernel.read(LATEST_PATH).value
        # now dispatch a manual second job and make the runner hang so no
        # fresh evidence exists; the old last_job evidence is invisible
        class SlowRunner:
            def __init__(self, inner):
                self.inner = inner

            def __call__(self, args, target):
                time.sleep(1.0)
                return self.inner(args, target)

        kernel.drivers["compute:flood-cnn-v2"].runner = SlowRunner(runner)
        result = kernel.act(cap.token, LATEST_PATH, RUN_FORECAST, {
            "stations": {"interval1": [[0.0, 1.0]]},
            "window_hours": 24,
            "total_m3s": 240.0,
            "last_observed_at": 999.0,
        })
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "dispatched")  # stale evidence invisible
        settle(kernel, lambda: kernel.txn(result.txn_id).state == "committed")
        self.assertNotEqual(kernel.read(LATEST_PATH).value, first_latest)


if __name__ == "__main__":
    unittest.main()
