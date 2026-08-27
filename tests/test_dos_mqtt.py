"""Tests for the dos MQTT transport and telemetry driver (no broker needed)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from dos import InvalidActionError, Kernel
from dos.mqtt import InMemoryMqttBus, MqttTelemetryDriver, topic_matches

STATION = "808J1510"
INTERVAL_PATH = f"/hydro/shanhu/stations/{STATION}/sampling_interval_seconds"
LEVEL_PATH = f"/hydro/shanhu/stations/{STATION}/level_m"
PREFIX = "water"


def telemetry(message_id: str, *, sequence: int = 1, **metrics) -> bytes:
    return json.dumps(
        {
            "message_id": message_id,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "sequence": sequence,
            "quality": "good",
            "metrics": {name: {"value": value, "unit": "x"} for name, value in metrics.items()},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def build(bus: InMemoryMqttBus) -> Kernel:
    from domains.flood.dos_instance import build_mqtt_kernel

    return build_mqtt_kernel(bus, {STATION}, topic_prefix=PREFIX)


class TestTopicMatching(unittest.TestCase):
    def test_wildcards(self):
        self.assertTrue(topic_matches("water/stations/+/telemetry", "water/stations/808J1510/telemetry"))
        self.assertFalse(topic_matches("water/stations/+/telemetry", "water/stations/808J1510/commands"))
        self.assertTrue(topic_matches("water/#", "water/stations/x/telemetry"))
        self.assertFalse(topic_matches("water/+/telemetry", "water/stations/808J1510/telemetry"))


class TestDriverNormalize(unittest.TestCase):
    def setUp(self):
        self.bus = InMemoryMqttBus()
        self.kernel = build(self.bus)
        self.driver = self.kernel.drivers["mqtt:water"]

    def test_metrics_commit_to_namespace(self):
        self.bus.inject(
            f"{PREFIX}/stations/{STATION}/telemetry",
            telemetry("m1", level_m=3.4, sampling_interval_seconds=600),
        )
        self.kernel.pump()
        self.assertEqual(self.kernel.read(LEVEL_PATH).value, 3.4)
        self.assertEqual(self.kernel.read(INTERVAL_PATH).value, 600)

    def test_duplicate_message_id_dropped(self):
        frame = telemetry("m1", level_m=3.4)
        self.bus.inject(f"{PREFIX}/stations/{STATION}/telemetry", frame)
        self.bus.inject(f"{PREFIX}/stations/{STATION}/telemetry", frame)  # QoS 1 redelivery
        self.kernel.pump()
        self.assertEqual(self.driver.received, 2)
        self.assertEqual(self.driver.dropped_duplicates, 1)
        self.assertEqual(self.kernel.read(LEVEL_PATH).generation, 1)

    def test_malformed_frame_dropped_not_fatal(self):
        self.bus.inject(f"{PREFIX}/stations/{STATION}/telemetry", b"not json")
        self.bus.inject(f"{PREFIX}/stations/UNKNOWN/telemetry", telemetry("m2", level_m=1.0))
        self.kernel.pump()
        self.assertEqual(self.driver.dropped_malformed, 2)
        self.assertFalse(self.kernel.namespace.exists(LEVEL_PATH))

    def test_unrelated_topic_ignored(self):
        self.bus.inject(f"{PREFIX}/stations/{STATION}/commands", b"{}")
        self.kernel.pump()
        self.assertEqual(self.driver.received, 0)


class TestCommandLoop(unittest.TestCase):
    def setUp(self):
        self.bus = InMemoryMqttBus()
        self.kernel = build(self.bus)
        self.cap = self.kernel.grant(
            f"/hydro/shanhu/stations/{STATION}", {"set_sampling_interval"}, "test"
        )

    def test_validate_rejects_bad_args_before_journaling(self):
        for bad in ({"seconds": 2}, {"seconds": 7200}, {"seconds": "60"}, {"seconds": True}, {}):
            with self.assertRaises(InvalidActionError):
                self.kernel.act(self.cap.token, INTERVAL_PATH, "set_sampling_interval", bad)
        self.assertEqual(self.kernel.consistency.pending(), [])  # nothing opened
        self.assertEqual([r for r in self.kernel.journal.replay() if r.kind == "txn"], [])

    def test_privileged_flow_publishes_command_and_commits_on_evidence(self):
        self.bus.inject(
            f"{PREFIX}/stations/{STATION}/telemetry",
            telemetry("m1", level_m=3.4, sampling_interval_seconds=600),
        )
        self.kernel.pump()
        result = self.kernel.act(
            self.cap.token, INTERVAL_PATH, "set_sampling_interval", {"seconds": 60},
            expect={INTERVAL_PATH: 600},
        )
        self.assertEqual(result.state, "awaiting_approval")
        result = self.kernel.approve(result.txn_id, approved_by="op", decision=True)
        self.assertEqual(result.state, "dispatched")
        # the device saw the downlink frame on the right topic
        topic, payload = self.bus.published[-1]
        self.assertEqual(topic, f"{PREFIX}/stations/{STATION}/commands")
        self.assertEqual(json.loads(payload)["operation"], "set_sampling_interval")
        # evidence arrives: the station reports the new interval
        self.bus.inject(
            f"{PREFIX}/stations/{STATION}/telemetry",
            telemetry("m2", sequence=2, level_m=3.5, sampling_interval_seconds=60),
        )
        self.kernel.pump()
        self.assertEqual(self.kernel.txn(result.txn_id).state, "committed")

    def test_monitor_process_end_to_end_over_bus(self):
        from domains.flood.dos_instance import STATUS_PATH, spawn_monitor

        spawn_monitor(self.kernel, self.cap.token)
        self.bus.inject(
            f"{PREFIX}/stations/{STATION}/telemetry",
            telemetry("m1", level_m=3.5, sampling_interval_seconds=600),
        )
        stats = self.kernel.pump()
        self.assertIn("level-monitor", stats["ran"])
        self.assertEqual(self.kernel.read(STATUS_PATH).value, "warning")
        pending = self.kernel.consistency.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].state, "awaiting_approval")


if __name__ == "__main__":
    unittest.main()
