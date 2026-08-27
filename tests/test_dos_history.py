"""Tests for the observation mirror (world-time indexed history)."""

from __future__ import annotations

import os
import tempfile
import unittest

from dos import Journal, Kernel
from dos.gateway import DosGateway, ReadScopeError
from dos.persistence import JsonlSink, load_journal, recover
from tests.test_dos_kernel import ValveDriver


def make_kernel() -> Kernel:
    kernel = Kernel(clock=lambda: 1000.0)
    kernel.mount("/plant", ValveDriver())
    kernel.drivers["valve-1"].privileged_actions = frozenset()
    return kernel


class TestMirror(unittest.TestCase):
    def test_history_indexed_by_world_time(self):
        kernel = make_kernel()
        kernel.interrupt("valve-1", {"position": "open", "ts": 2000.0})
        kernel.interrupt("valve-1", {"position": "closed", "ts": 2100.0})
        kernel.pump()
        samples = kernel.history("/plant/valve-1/position")
        self.assertEqual([(s.observed_at, s.value) for s in samples], [(2000.0, "open"), (2100.0, "closed")])
        window = kernel.history("/plant/valve-1/position", since=2050.0)
        self.assertEqual([s.value for s in window], ["closed"])
        self.assertEqual(kernel.mirror.last_observed_at("/plant/valve-1/position"), 2100.0)

    def test_missing_world_time_falls_back_to_system_time(self):
        kernel = make_kernel()  # clock frozen at 1000
        kernel.interrupt("valve-1", {"position": "open"})  # no ts
        kernel.pump()
        samples = kernel.history("/plant/valve-1/position")
        self.assertEqual(samples[0].observed_at, 1000.0)

    def test_retention_and_limit(self):
        kernel = Kernel(clock=lambda: 0.0, history_retention_seconds=100.0)
        kernel.mount("/plant", ValveDriver())
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        for index in range(10):
            kernel.interrupt("valve-1", {"position": f"p{index}", "ts": float(index)})
        kernel.pump()
        # retention sweeps entries older than newest-100s... newest is 9.0,
        # horizon = -91 → nothing swept; use limit instead
        recent = kernel.history("/plant/valve-1/position", limit=3)
        self.assertEqual([s.value for s in recent], ["p7", "p8", "p9"])
        # and with a long tail, time retention kicks in
        kernel.interrupt("valve-1", {"position": "far", "ts": 10000.0})
        kernel.pump()
        kept = [s.value for s in kernel.history("/plant/valve-1/position")]
        self.assertEqual(kept, ["far"])  # everything older than 9900s swept

    def test_mirror_rebuilt_on_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "journal.jsonl")
            now = {"t": 500.0}
            k1 = Kernel(
                journal=Journal(clock=lambda: now["t"], sink=JsonlSink(path)),
                clock=lambda: now["t"],
            )
            k1.mount("/plant", ValveDriver())
            k1.drivers["valve-1"].privileged_actions = frozenset()
            k1.interrupt("valve-1", {"position": "open", "ts": 2000.0})
            k1.interrupt("valve-1", {"position": "closed", "ts": 2100.0})
            k1.pump()

            k2 = Kernel(journal=load_journal(path), clock=lambda: now["t"])
            k2.mount("/plant", ValveDriver())
            k2.drivers["valve-1"].privileged_actions = frozenset()
            stats = recover(k2)
            self.assertEqual(stats["observations"], 2)
            self.assertEqual(
                [(s.observed_at, s.value) for s in k2.history("/plant/valve-1/position")],
                [(2000.0, "open"), (2100.0, "closed")],
            )

    def test_mqtt_driver_observed_at_flows_to_mirror(self):
        from datetime import datetime, timezone

        from dos.mqtt import InMemoryMqttBus
        from domains.flood.dos_instance import build_mqtt_kernel

        bus = InMemoryMqttBus()
        kernel = build_mqtt_kernel(bus, {"808J1510"})
        frame = (
            b'{"message_id":"m1","observed_at":"2026-08-01T00:00:00+00:00","metrics":{"level_m":{"value":3.1}}}'
        )
        bus.inject("water/stations/808J1510/telemetry", frame)
        kernel.pump()
        samples = kernel.history("/hydro/shanhu/stations/808J1510/level_m")
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0].observed_at, datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())

    def test_malformed_observed_at_dropped(self):
        from dos.mqtt import InMemoryMqttBus
        from domains.flood.dos_instance import build_mqtt_kernel

        bus = InMemoryMqttBus()
        kernel = build_mqtt_kernel(bus, {"808J1510"})
        frame = b'{"message_id":"m1","observed_at":"not-a-date","metrics":{"level_m":1}}'
        bus.inject("water/stations/808J1510/telemetry", frame)
        kernel.pump()
        self.assertEqual(kernel.drivers["mqtt:water"].dropped_malformed, 1)
        self.assertFalse(kernel.namespace.exists("/hydro/shanhu/stations/808J1510/level_m"))


class TestGatewayHistory(unittest.TestCase):
    def test_history_scoped(self):
        kernel = make_kernel()
        kernel.interrupt("valve-1", {"position": "open", "ts": 2000.0})
        kernel.pump()
        gw = DosGateway(kernel)
        scoped = gw.open_session("scoped", read_scopes=("/plant",))
        rows = gw.history(scoped.session_id, "/plant/valve-1/position")
        self.assertEqual(rows[0]["value"], "open")
        self.assertEqual(rows[0]["observed_at"], 2000.0)
        with self.assertRaises(ReadScopeError):
            gw.history(scoped.session_id, "/other/path")


if __name__ == "__main__":
    unittest.main()
