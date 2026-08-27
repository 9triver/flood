"""Impact assessment closed loop on the dos kernel (fake analysis runner)."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from dos import InvalidActionError, Kernel
from dos.mqtt import InMemoryMqttBus
from domains.flood.dos_forecast import (
    BOUNDARIES,
    FORECAST_MOUNT,
    LATEST_PATH as FORECAST_LATEST,
    RUN_FORECAST,
    mount_forecast,
    spawn_forecast_trigger,
)
from domains.flood.dos_impact import (
    ANALYZE_IMPACT,
    IMPACT_LATEST,
    IMPACT_MOUNT,
    IMPACT_PENDING,
    mount_impact,
    spawn_impact_auto,
)
from domains.flood.dos_instance import build_mqtt_kernel
from tests.test_dos_forecast import FakeRunner, feed, settle

STANDARD_TARGETS = [{"type": "bridge", "id": "bridge-1"}, {"type": "point", "id": "village-a"}]


class FakeImpactRunner:
    def __init__(self, error: str | None = None):
        self.error = error
        self.calls = []

    def __call__(self, args: dict, target: Path, forecast_meta: dict) -> dict:
        self.calls.append((args, forecast_meta))
        if self.error:
            raise RuntimeError(self.error)
        return {
            "summary": {"affected": 2, "by_risk": {"high": 1, "medium": 1}},
            "highlights": [{"id": "bridge-1", "risk": "high"}],
            "artifacts": {"geojson": str(target / "impact.geojson")},
        }


def build(forecast_runner, impact_runner):
    bus = InMemoryMqttBus()
    kernel = build_mqtt_kernel(bus, set(BOUNDARIES), topic_prefix="water")
    mount_forecast(kernel, forecast_runner, artifact_root=Path("/tmp/dos-impact-test") / str(time.time()))
    mount_impact(kernel, impact_runner, artifact_root=Path("/tmp/dos-impact-test") / str(time.time()))
    forecast_cap = kernel.grant(FORECAST_MOUNT, {RUN_FORECAST}, "test")
    impact_cap = kernel.grant(IMPACT_MOUNT, {ANALYZE_IMPACT}, "test")
    events: list[str] = []
    spawn_forecast_trigger(kernel, forecast_cap.token)
    spawn_impact_auto(kernel, impact_cap.token, targets=STANDARD_TARGETS, sink=events)
    return kernel, bus, forecast_cap, impact_cap, events


def make_forecast(kernel, bus):
    for hour in range(25):
        feed(bus, hour, {b: 60.0 if hour >= 24 else 10.0 for b in BOUNDARIES})
    settle(kernel, lambda: kernel.try_read(FORECAST_LATEST) is not None)
    return kernel.read(FORECAST_LATEST).value["id"]


class TestImpactLoop(unittest.TestCase):
    def test_auto_sweep_after_forecast(self):
        impact = FakeImpactRunner()
        kernel, bus, fcap, icap, events = build(FakeRunner(), impact)
        forecast_id = make_forecast(kernel, bus)
        settle(kernel, lambda: kernel.try_read(IMPACT_LATEST) is not None)
        latest = kernel.read(IMPACT_LATEST).value
        self.assertEqual(latest["forecast_id"], forecast_id)
        meta = kernel.read(f"{IMPACT_MOUNT}/{latest['id']}").value
        self.assertEqual(meta["summary"]["affected"], 2)
        self.assertEqual(meta["targets"], STANDARD_TARGETS)
        self.assertEqual(impact.calls[0][0]["forecast_id"], forecast_id)
        self.assertIn("artifacts", impact.calls[0][1])  # runner got forecast meta
        self.assertEqual(kernel.read(IMPACT_PENDING).value, {})
        self.assertEqual(len(impact.calls), 1)

    def test_no_duplicate_sweep_for_same_forecast(self):
        impact = FakeImpactRunner()
        kernel, bus, fcap, icap, events = build(FakeRunner(), impact)
        make_forecast(kernel, bus)
        settle(kernel, lambda: kernel.try_read(IMPACT_LATEST) is not None)
        # a duplicate frame re-commits forecasts/latest; the sweep must not re-run
        from tests.test_dos_forecast import feed as f

        f(bus, 24, {b: 61.0 for b in BOUNDARIES})
        kernel.pump()
        self.assertEqual(len(impact.calls), 1)

    def test_unknown_forecast_rejected(self):
        impact = FakeImpactRunner()
        kernel, bus, fcap, icap, events = build(FakeRunner(), impact)
        with self.assertRaises(InvalidActionError) as ctx:
            kernel.act(icap.token, IMPACT_LATEST, ANALYZE_IMPACT, {"forecast_id": "fcst_999999", "targets": STANDARD_TARGETS})
        self.assertIn("unknown forecast", str(ctx.exception))

    def test_impact_failure_is_explicit(self):
        impact = FakeImpactRunner(error="grid unavailable")
        kernel, bus, fcap, icap, events = build(FakeRunner(), impact)
        make_forecast(kernel, bus)
        settle(kernel, lambda: any(t.state == "failed" for t in list(kernel.consistency._pending.values())))
        self.assertIsNone(kernel.try_read(IMPACT_LATEST))


if __name__ == "__main__":
    unittest.main()
