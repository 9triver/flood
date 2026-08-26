from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from domains.flood.runtime.boundary_flow import (
    BOUNDARIES,
    DEFAULT_BOUNDARY_FLOW_CSV_PATH,
)
from domains.flood.runtime.playback_sources import PlaybackSourceRegistry
from server.domain_playback import DomainPlaybackController
from server.domain_runtime_host import DomainRuntimeHost


@dataclass(frozen=True)
class FakeEvent:
    event_type: str


class FakeRuntime:
    domain_id = "water.flood"

    def __init__(self) -> None:
        self._events: list[FakeEvent] = []

    def events(self):
        return tuple(self._events)


class FakePlaybackSystem:
    def __init__(self, rows: list[dict], trigger_sequences: set[int]) -> None:
        self.runtime = FakeRuntime()
        self.rows = tuple(rows)
        self.trigger_sequences = set(trigger_sequences)
        self.index = 0
        self.run_id = "initial-run"
        self.source_ref = "test"
        self.owner_thread_id = threading.get_ident()
        self.advance_thread_ids: list[int] = []
        self.reset_thread_ids: list[int] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def advance(self):
        self.advance_thread_ids.append(threading.get_ident())
        if self.index >= len(self.rows):
            return None
        row = dict(self.rows[self.index])
        if self.index in self.trigger_sequences:
            self.runtime._events.extend((
                FakeEvent("water.flood.forecast.required"),
                FakeEvent("water.flood.forecast.generated"),
            ))
        self.index += 1
        return row

    def evolution_status(self):
        return {
            "evolution_run_id": self.run_id,
            "source_ref": self.source_ref,
            "sequence": self.index - 1 if self.index else None,
            "next_sequence": self.index,
            "total_rows": len(self.rows),
            "has_next": self.index < len(self.rows),
            "next_step_forecast_triggered": self.index in self.trigger_sequences,
        }

    def reset_evolution(self, rows, *, run_id=None, source_ref="test"):
        self.reset_thread_ids.append(threading.get_ident())
        self.rows = tuple(rows)
        self.index = 0
        self.run_id = str(run_id)
        self.source_ref = source_ref
        return self.evolution_status()


class DomainPlaybackControllerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        registry = PlaybackSourceRegistry(
            root=Path(self.tempdir.name) / "sources",
            builtin_path=DEFAULT_BOUNDARY_FLOW_CSV_PATH,
        )
        self.systems: list[FakePlaybackSystem] = []

        def factory():
            system = FakePlaybackSystem(_rows(), {0})
            self.systems.append(system)
            return system, None

        self.host = DomainRuntimeHost(factory)
        self.host.start()
        self.controller = DomainPlaybackController(
            self.host,
            registry,
            base_interval_seconds=60,
        )

    def tearDown(self):
        self.controller.close()
        self.host.stop()
        self.tempdir.cleanup()

    def test_automatic_forecast_pauses_and_all_mutations_use_host_thread(self):
        started = self.controller.start_playback(20)
        self.assertEqual("domain_os", started["runtime_mode"])

        status = self._wait_for_phase("paused")

        system = self.systems[0]
        self.assertEqual(1, system.index)
        self.assertEqual(1, status["forecast_version"])
        self.assertEqual(1, status["completed_forecast_version"])
        self.assertTrue(status["step_available"])
        self.assertEqual(
            [self.host.thread_id],
            list(set(system.advance_thread_ids)),
        )
        self.assertEqual(
            [self.host.thread_id],
            list(set(system.reset_thread_ids)),
        )
        self.assertTrue(any(
            item["event"] == "boundary_flow_data"
            and item["data"]["event"]["event_type"]
            == "water.flood.evolution.advanced"
            for item in self.controller.outputs
        ))
        self.assertFalse(any(
            item["event"] == "domain_event"
            for item in self.controller.outputs
        ))

    def test_step_advances_once_and_reset_creates_new_domain_run(self):
        self.controller.start_playback(20)
        first = self._wait_for_phase("paused")
        first_run_id = first["evolution_run_id"]

        stepped = self.controller.step_playback()

        self.assertTrue(stepped["stepped"])
        self.assertFalse(stepped["forecast_triggered"])
        self.assertEqual("paused", stepped["playback_phase"])
        self.assertEqual(2, self.systems[0].index)

        reset = self.controller.restart_playback(10)

        self.assertEqual("reset", reset["status"])
        self.assertEqual("ready", reset["playback_phase"])
        self.assertEqual(0, self.systems[0].index)
        self.assertNotEqual(first_run_id, reset["evolution_run_id"])
        self.assertEqual(10, reset["speed_multiplier"])

    def test_invalid_transitions_and_auto_pause_validation_match_legacy_api(self):
        with self.assertRaisesRegex(ValueError, "not paused"):
            self.controller.resume_playback(10)
        with self.assertRaisesRegex(ValueError, "暂停状态"):
            self.controller.step_playback()
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            self.controller.set_auto_pause("false")
        with self.assertRaisesRegex(ValueError, "one of"):
            self.controller.set_playback_speed(3)

    def test_new_run_is_rejected_while_domain_products_are_processing(self):
        async def mark_processing(system):
            await self.controller._ensure_worker(system)
            self.controller._playback_phase = "processing"

        self.host.call_system(mark_processing)

        with self.assertRaisesRegex(ValueError, "不能开始"):
            self.controller.start_playback(20)
        with self.assertRaisesRegex(ValueError, "不能重置"):
            self.controller.restart_playback(20)

    def _wait_for_phase(self, phase: str):
        deadline = time.monotonic() + 2
        with self.controller.condition:
            while self.controller.status()["playback_phase"] != phase:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail(f"playback did not reach {phase}")
                self.controller.condition.wait(timeout=remaining)
            return self.controller.status()


def _rows(count: int = 25) -> list[dict]:
    start = datetime(2026, 8, 26, tzinfo=timezone(timedelta(hours=8)))
    rows = []
    for sequence in range(count):
        observed_at = (start + timedelta(hours=sequence)).isoformat()
        rows.append({
            "sequence": sequence,
            "observed_at": observed_at,
            "simulation_time": observed_at,
            "rainfall_mm": 0.0,
            "station_rainfall": [],
            "reservoir_inflow_m3s": 30.0,
            "reservoir_release_m3s": 20.0,
            "reservoir_level_m": 245.0,
            "boundaries": {
                key: {"label": label, "flow_m3s": 10.0}
                for key, label in BOUNDARIES.items()
            },
            "baseflow_total_m3s": 0.5,
            "total_flow_m3s": 40.0,
        })
    return rows


if __name__ == "__main__":
    unittest.main()
