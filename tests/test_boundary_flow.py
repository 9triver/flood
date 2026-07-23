from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from domains.flood.runtime.boundary_flow import (
    BASE_FLOWS_M3S,
    BoundaryFlowPlayback,
    BoundaryFlowPlaybackSource,
    FloodForecastPolicy,
)
from domains.flood.runtime.workspace import WorkspaceManager
from server.event_runtime import (
    adaptive_playback_speed,
    BoundaryFlowPlaybackRunner,
    EventRuntime,
    INUNDATION_EVENT_TOOLS,
    filter_inundation_map_event,
)


CSV_PATH = PROJECT_DIR / "domains" / "flood" / "data" / "mock" / "boundary_flow.csv"


class BoundaryFlowPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temporary.name)
        self.source = BoundaryFlowPlaybackSource(
            CSV_PATH,
            self.temp_dir / "observations.jsonl",
        )
        self.policy = FloodForecastPolicy(
            self.source.rows,
            forecast_input_dir=self.temp_dir / "forecast_inputs",
            latest_forecast_input_path=self.temp_dir / "latest_forecast_input.json",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_csv_parsing_derives_tonggu_and_has_baseflow_lead_in(self):
        self.assertGreater(len(self.source.rows), 72)
        lead_in_flows = {key: [] for key in BASE_FLOWS_M3S}
        for row in self.source.rows[:72]:
            self.assertEqual(row["rainfall_mm"], 0)
            self.assertGreater(row["total_flow_m3s"], 0)
            for key, reference in BASE_FLOWS_M3S.items():
                flow = row["boundaries"][key]["flow_m3s"]
                lead_in_flows[key].append(flow)
                self.assertGreater(flow, reference * 0.85)
                self.assertLess(flow, reference * 1.15)
            self.assertEqual(self.policy.observe(row), [])
        for flows in lead_in_flows.values():
            self.assertGreater(len(set(flows)), 12)
            self.assertGreater(max(flows) - min(flows), 0)
        self.assertEqual(self.policy.state, FloodForecastPolicy.NORMAL)
        self.assertEqual(self.policy.episode_id, "")

        flood_row = next(row for row in self.source.rows if row["observed_at"].startswith("2025-01-01T08:00"))
        interval2 = flood_row["boundaries"]["interval2"]["flow_m3s"]
        tonggu = flood_row["boundaries"]["tonggu"]["flow_m3s"]
        self.assertAlmostEqual(tonggu, interval2 * 0.946, places=6)

    def test_initial_trigger_and_stable_25_point_forecast_input(self):
        events = self._play_all()
        requests = [event for event in events if event["event_type"] == "FloodForecastRequired"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["time"], "2025-01-01T08:00:00+08:00")

        summary = requests[0]["payload"]["forecast_input"]
        self.assertEqual(summary["version"], 1)
        self.assertEqual(summary["window_start"], "2025-01-01T03:00:00+08:00")
        self.assertEqual(summary["window_end"], "2025-01-02T03:00:00+08:00")
        self.assertEqual(summary["observed_point_count"], 6)
        self.assertEqual(summary["forecast_point_count"], 19)
        for boundary in summary["boundaries"].values():
            self.assertEqual(len(boundary["series"]), 25)
            self.assertEqual(boundary["series"][0]["time_h"], 0)
            self.assertEqual(boundary["series"][-1]["time_h"], 24)

    def test_active_episode_does_not_repeat_initial_request(self):
        request = self._play_until_request()
        input_id = request["source_id"]
        self.assertTrue(self.policy.mark_forecast_started(input_id))
        self.assertFalse(self.policy.mark_forecast_started(input_id))
        self.assertTrue(self.policy.mark_forecast_completed(input_id))

        later_events = self._play_all()
        requests = [event for event in later_events if event["event_type"] == "FloodForecastRequired"]
        self.assertEqual(requests, [])
        self.assertEqual(self.policy.version, 1)

    def test_two_deviating_periods_after_cooldown_create_version_two(self):
        request = self._play_until_request()
        self.policy.mark_forecast_started(request["source_id"])
        self.policy.mark_forecast_completed(request["source_id"])

        recompute = None
        while recompute is None:
            observation = self.source.next_observation()
            self.assertIsNotNone(observation)
            if observation["observed_at"].startswith(("2025-01-01T11:00", "2025-01-01T12:00")):
                observation = _scale_observation(observation, 1.5)
            for event in self.policy.observe(observation):
                if event["event_type"] == "FloodForecastRequired":
                    recompute = event
                    break

        self.assertEqual(recompute["time"], "2025-01-01T12:00:00+08:00")
        self.assertEqual(recompute["payload"]["forecast_trigger"]["trigger_type"], "deviation")
        self.assertEqual(recompute["payload"]["forecast_input"]["version"], 2)
        self.assertTrue((self.temp_dir / "forecast_inputs" / "flood_20250101T0300" / "v002.json").exists())

    def test_pending_request_is_coalesced(self):
        request = self._play_until_request()
        self.assertTrue(self.policy.mark_forecast_started(request["source_id"]))
        events = self._play_all()
        requests = [event for event in events if event["event_type"] == "FloodForecastRequired"]
        self.assertEqual(requests, [])
        self.assertEqual(self.policy.version, 1)

    def test_three_clear_observations_close_episode_and_reset_allows_another(self):
        request = self._play_until_request()
        self.policy.mark_forecast_started(request["source_id"])
        self.policy.mark_forecast_completed(request["source_id"])
        start = datetime.fromisoformat(request["time"])
        end_events = []
        for offset in range(1, 4):
            end_events.extend(self.policy.observe(_clear_observation(start + timedelta(hours=offset), offset)))

        self.assertEqual([event["event_type"] for event in end_events], ["FloodEpisodeEnded"])
        self.assertEqual(self.policy.state, FloodForecastPolicy.CLOSED)

        self.source.reset()
        self.policy.reset()
        second_request = self._play_until_request()
        self.assertEqual(second_request["time"], "2025-01-01T08:00:00+08:00")
        self.assertEqual(self.policy.version, 1)

    def _play_until_request(self):
        while True:
            observation = self.source.next_observation()
            self.assertIsNotNone(observation)
            for event in self.policy.observe(observation):
                if event["event_type"] == "FloodForecastRequired":
                    return event

    def _play_all(self):
        events = []
        while (observation := self.source.next_observation()) is not None:
            events.extend(self.policy.observe(observation))
        return events


class BoundaryFlowPlaybackRunnerTest(unittest.TestCase):
    def test_speed_multiplier_changes_playback_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = BoundaryFlowPlaybackSource(CSV_PATH, Path(temporary) / "observations.jsonl")
            runner = BoundaryFlowPlaybackRunner(BoundaryFlowPlayback(source), interval_seconds=5)
            self.assertEqual(runner.interval_seconds, 0.25)
            self.assertEqual(runner.set_speed(2), 2)
            self.assertEqual(runner.interval_seconds, 2.5)
            self.assertEqual(runner.set_speed(10), 10)
            self.assertEqual(runner.interval_seconds, 0.5)
            self.assertEqual(runner.set_speed(20), 20)
            self.assertEqual(runner.interval_seconds, 0.25)
            with self.assertRaises(ValueError):
                runner.set_speed(3)

    def test_adaptive_speed_follows_baseline_rainfall_and_forecast_phases(self):
        dry = {"rainfall_mm": 0}
        raining = {"rainfall_mm": 1.6}

        self.assertEqual(adaptive_playback_speed(dry, "NORMAL")[:2], (20.0, "baseline"))
        self.assertEqual(adaptive_playback_speed(raining, "RISING")[:2], (10.0, "rainfall"))
        self.assertEqual(adaptive_playback_speed(raining, "PENDING")[:2], (5.0, "forecast"))

    def test_adaptive_speed_transitions_match_the_mock_flood_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp_dir = Path(temporary)
            source = BoundaryFlowPlaybackSource(CSV_PATH, temp_dir / "observations.jsonl")
            policy = FloodForecastPolicy(
                source.rows,
                forecast_input_dir=temp_dir / "forecast_inputs",
                latest_forecast_input_path=temp_dir / "latest.json",
            )
            playback = BoundaryFlowPlayback(source, policy)
            speed = 20.0
            transitions = []

            while speed > 5:
                event, _ = playback.next_events()
                self.assertIsNotNone(event)
                observation = event["payload"]["observation"]
                target, phase, _ = adaptive_playback_speed(observation, policy.state)
                if speed > target:
                    speed = target
                    transitions.append((observation["sequence"], speed, phase))

            self.assertEqual(
                transitions,
                [(73, 10.0, "rainfall"), (79, 5.0, "forecast")],
            )

    def test_runner_continues_after_forecast_request_until_csv_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            temp_dir = Path(temporary)
            source = BoundaryFlowPlaybackSource(CSV_PATH, temp_dir / "observations.jsonl")
            policy = FloodForecastPolicy(
                source.rows,
                forecast_input_dir=temp_dir / "forecast_inputs",
                latest_forecast_input_path=temp_dir / "latest.json",
            )
            runner = BoundaryFlowPlaybackRunner(
                BoundaryFlowPlayback(source, policy),
                interval_seconds=0,
            )
            observations = []
            policy_events = []
            finished = []
            runner.play_generation(
                generation=1,
                is_running=lambda generation: generation == 1,
                publish_observation=observations.append,
                publish_policy_event=policy_events.append,
                finish_sequence=lambda generation, event: finished.append((generation, event)),
                sleep_while_running=lambda seconds, generation: None,
            )

            self.assertEqual(len(observations), len(source.rows))
            self.assertEqual(
                len([event for event in policy_events if event["event_type"] == "FloodForecastRequired"]),
                1,
            )
            self.assertEqual(len(finished), 1)
            self.assertEqual(finished[0][1]["event_type"], "BoundaryFlowObserved")


class EventRuntimePlaybackControlTest(unittest.TestCase):
    def test_adaptive_speed_only_slows_the_running_playback(self):
        runtime = EventRuntime(object())
        runtime._started = True
        runtime._playback_running = True
        runtime._boundary_flow_runner.set_speed(20)
        runtime._boundary_flow_runner.playback.policy.state = "RISING"

        runtime._apply_adaptive_playback_speed({"rainfall_mm": 1.6})
        self.assertEqual(runtime._boundary_flow_runner.speed_multiplier, 10)
        self.assertEqual(runtime.outputs[-1]["data"]["speed_phase"], "rainfall")

        runtime._boundary_flow_runner.playback.policy.state = "PENDING"
        runtime._apply_adaptive_playback_speed({"rainfall_mm": 24})
        self.assertEqual(runtime._boundary_flow_runner.speed_multiplier, 5)
        self.assertEqual(runtime.outputs[-1]["data"]["speed_phase"], "forecast")

        output_count = len(runtime.outputs)
        runtime._boundary_flow_runner.set_speed(2)
        runtime._apply_adaptive_playback_speed({"rainfall_mm": 24})
        self.assertEqual(runtime._boundary_flow_runner.speed_multiplier, 2)
        self.assertEqual(len(runtime.outputs), output_count)

    def test_pause_preserves_generation_and_pending_agent_events(self):
        runtime = EventRuntime(object())
        runtime._started = True
        runtime._playback_running = True
        runtime._generation = 7
        queued_event = {"event_type": "InundationGenerated", "event_id": "evt_test"}
        runtime._event_queue.append((queued_event, 7))

        status = runtime.pause_playback()

        self.assertFalse(status["running"])
        self.assertEqual(status["status"], "paused")
        self.assertEqual(runtime._generation, 7)
        self.assertEqual(list(runtime._event_queue), [(queued_event, 7)])
        self.assertEqual(runtime.outputs[-1]["data"]["status"], "paused")

        source_index = runtime._boundary_flow_runner.playback.source.index
        resumed = runtime.resume_playback(10)

        self.assertTrue(resumed["running"])
        self.assertFalse(resumed["paused"])
        self.assertEqual(runtime._generation, 7)
        self.assertEqual(runtime._boundary_flow_runner.playback.source.index, source_index)
        self.assertEqual(list(runtime._event_queue), [(queued_event, 7)])

    def test_restart_creates_new_workspace_and_rewinds_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            first = manager.create()["workspace_id"]
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._playback_running = True
            runtime._generation = 4
            runtime._boundary_flow_runner.playback.source.index = 5

            with patch("server.event_runtime.WORKSPACES", manager):
                with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                    status = runtime.restart_playback(10)

            self.assertTrue(status["running"])
            self.assertEqual(status["speed_multiplier"], 10)
            self.assertEqual(runtime._boundary_flow_runner.playback.source.index, 0)
            self.assertEqual(status["workspace_id"], manager.active_id)
            self.assertNotEqual(status["workspace_id"], first)
            first_manifest = json.loads(
                (manager.path(first) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_manifest["status"], "stopped")


class InundationMapEventTest(unittest.TestCase):
    def test_inundation_event_agent_cannot_call_impact_analysis(self):
        self.assertNotIn("analyze_inundation_impacts", INUNDATION_EVENT_TOOLS)

    def test_only_hydrodynamic_actions_reach_automatic_frontend_stream(self):
        event = {
            "type": "map_actions",
            "context": "预测淹没影响",
            "map_actions": [
                {"type": "show_hydrodynamic_mesh"},
                {"type": "apply_hydrodynamic_result", "filters": {"forecast_id": "latest"}},
                {"type": "load_object", "object_type": "Route"},
                {"type": "clear_highlights"},
                {"type": "highlight_objects", "object_type": "Route"},
            ],
            "result_cards": [{"title": "受影响路线"}],
        }

        filtered = filter_inundation_map_event(event)

        self.assertEqual(
            [action["type"] for action in filtered["map_actions"]],
            ["show_hydrodynamic_mesh", "apply_hydrodynamic_result"],
        )
        self.assertEqual(filtered["result_cards"], [])

    def test_impact_only_map_event_is_suppressed(self):
        event = {
            "type": "map_actions",
            "map_actions": [{"type": "load_object", "object_type": "Facility"}],
        }

        self.assertIsNone(filter_inundation_map_event(event))


def _scale_observation(observation, scale):
    result = copy.deepcopy(observation)
    for boundary in result["boundaries"].values():
        boundary["flow_m3s"] = round(float(boundary["flow_m3s"]) * scale, 6)
    result["total_flow_m3s"] = round(
        sum(boundary["flow_m3s"] for boundary in result["boundaries"].values()),
        6,
    )
    return result


def _clear_observation(observed_at, sequence):
    boundaries = {
        key: {"label": label, "flow_m3s": 0.0}
        for key, label in {
            "interval1": "区间1",
            "interval2": "区间2",
            "tonggu": "同古河",
            "upstream": "坝址",
        }.items()
    }
    return {
        "sequence": sequence,
        "observed_at": observed_at.isoformat(),
        "rainfall_mm": 0.0,
        "reservoir_level_m": 245.1,
        "boundaries": boundaries,
        "total_flow_m3s": 0.0,
    }


if __name__ == "__main__":
    unittest.main()
