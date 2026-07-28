from __future__ import annotations

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
from domains.flood.runtime.reservoir_monitoring import assess_reservoir_window
from domains.flood.runtime.workspace import WorkspaceManager
from oag.ontology.prompt_builder import OntologyPromptBuilder
from oag.ontology.registry import FunctionRegistry
from oag.ontology.schema import Ontology
from server.events import EventRuntime
from server.events.playback import (
    BoundaryFlowPlaybackRunner,
    adaptive_playback_speed,
)
from server.presentation.event_maps import filter_event_map_event


CSV_PATH = PROJECT_DIR / "domains" / "flood" / "data" / "mock" / "boundary_flow.csv"
ONTOLOGY = Ontology.load(PROJECT_DIR / "domains" / "flood" / "ontology.yaml")


class BoundaryFlowPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temporary.name)
        self.source = BoundaryFlowPlaybackSource(
            CSV_PATH,
            self.temp_dir / "observations.jsonl",
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
        for flows in lead_in_flows.values():
            self.assertGreater(len(set(flows)), 12)
            self.assertGreater(max(flows) - min(flows), 0)

        flood_row = next(row for row in self.source.rows if row["observed_at"].startswith("2025-01-01T08:00"))
        interval2 = flood_row["boundaries"]["interval2"]["flow_m3s"]
        tonggu = flood_row["boundaries"]["tonggu"]["flow_m3s"]
        self.assertAlmostEqual(tonggu, interval2 * 0.946, places=6)

    def test_observation_includes_next_24_hours_of_station_rainfall(self):
        for index, row in enumerate(self.source.rows[:25]):
            row["station_rainfall"] = [{
                "station_id": "weather_1",
                "name": "测试气象站",
                "rainfall_mm": float(index),
                "derivation_method": "test",
            }]

        observation = self.source.next_observation()

        self.assertIsNotNone(observation)
        forecasts = observation["station_rainfall_forecast"]
        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0]["station_id"], "weather_1")
        self.assertEqual(len(forecasts[0]["series"]), 24)
        self.assertEqual(
            forecasts[0]["series"][0],
            {
                "valid_time": self.source.rows[1]["simulation_time"],
                "rainfall_mm": 1.0,
            },
        )
        self.assertEqual(
            forecasts[0]["series"][-1],
            {
                "valid_time": self.source.rows[24]["simulation_time"],
                "rainfall_mm": 24.0,
            },
        )
        stored = json.loads(self.source.observation_path.read_text(encoding="utf-8"))
        self.assertEqual(
            stored["station_rainfall_forecast"],
            forecasts,
        )

    def test_observation_includes_future_rainfall_and_boundary_flow_window(self):
        for index, row in enumerate(self.source.rows[:25]):
            row["rainfall_mm"] = float(index)
            for boundary in row["boundaries"].values():
                boundary["flow_m3s"] = float(index * 10)
            row["total_flow_m3s"] = float(index * 40)

        observation = self.source.next_observation()

        self.assertIsNotNone(observation)
        rainfall = observation["rainfall_forecast"]
        self.assertEqual(len(rainfall), 24)
        self.assertEqual(rainfall[0]["rainfall_mm"], 1.0)
        self.assertEqual(rainfall[-1]["rainfall_mm"], 24.0)

        flow = observation["boundary_flow_forecast"]
        self.assertEqual(flow["window_start"], self.source.rows[0]["simulation_time"])
        self.assertEqual(flow["window_end"], self.source.rows[24]["simulation_time"])
        self.assertEqual(flow["window_hours"], 24)
        self.assertEqual(flow["threshold_m3s"], 230.0)
        self.assertEqual(flow["peak_total_flow_m3s"], 960.0)
        self.assertEqual(flow["peak_at"], self.source.rows[24]["simulation_time"])
        self.assertEqual(
            flow["first_threshold_exceeded_at"],
            self.source.rows[6]["simulation_time"],
        )
        self.assertEqual(len(flow["series"]), 24)
        self.assertEqual(
            flow["series"][0]["boundaries"]["interval1"]["flow_m3s"],
            10.0,
        )

    def test_observation_includes_next_24_hours_of_reservoir_forecast(self):
        for index, row in enumerate(self.source.rows[:25]):
            row["reservoir_inflow_m3s"] = float(index + 10)
            row["reservoir_release_m3s"] = float(index + 5)
            row["reservoir_level_m"] = 245.0 + index * 0.1

        observation = self.source.next_observation()

        self.assertIsNotNone(observation)
        forecast = observation["reservoir_forecast"]
        self.assertEqual(forecast["reservoir_id"], "longtan")
        self.assertEqual(forecast["station_id"], "HP0014511220000128")
        self.assertEqual(len(forecast["series"]), 24)
        self.assertEqual(
            forecast["series"][0],
            {
                "valid_time": self.source.rows[1]["simulation_time"],
                "reservoir_inflow_m3s": 11.0,
                "reservoir_release_m3s": 6.0,
                "reservoir_level_m": 245.1,
                "status": {
                    "key": "normal",
                    "label": "正常",
                    "color": "#238b57",
                },
            },
        )
        self.assertEqual(
            forecast["series"][-1]["valid_time"],
            self.source.rows[24]["simulation_time"],
        )
        assessment = forecast["assessment"]
        self.assertEqual(assessment["window_hours"], 24)
        self.assertEqual(assessment["peak"]["level_m"], 247.4)
        self.assertEqual(assessment["peak"]["status"]["key"], "warning")
        self.assertIsNone(assessment["alert"])

        stored = json.loads(self.source.observation_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["reservoir_forecast"], forecast)

    def test_reservoir_level_status_uses_ontology_threshold_boundaries(self):
        expected = (
            (245.29, "normal"),
            (245.30, "warning"),
            (247.92, "danger"),
            (248.91, "danger"),
            (248.911, "critical"),
        )
        for level, status in expected:
            with self.subTest(level=level):
                assessment = assess_reservoir_window(
                    {
                        "simulation_time": "2026-07-01T00:00:00+08:00",
                        "reservoir_level_m": level,
                    },
                    [],
                )
                self.assertEqual(assessment["current"]["status"]["key"], status)

    def test_reservoir_window_returns_only_the_highest_alert(self):
        expected = (
            (247.61, None),
            (247.62, "为保障大坝安全，按泄洪设施下泄能力下泄"),
            (248.61, "入库流量持续加大，水位逼近校核洪水位"),
            (248.911, "警告：水位超校核洪水位！大坝安全面临严峻考验"),
        )
        for level, alert_text in expected:
            with self.subTest(level=level):
                assessment = assess_reservoir_window(
                    {
                        "simulation_time": "2026-07-01T00:00:00+08:00",
                        "reservoir_level_m": 245.1,
                    },
                    [{
                        "valid_time": "2026-07-01T01:00:00+08:00",
                        "reservoir_level_m": level,
                    }],
                )
                actual = assessment["alert"]["text"] if assessment["alert"] else None
                self.assertEqual(actual, alert_text)
                if assessment["alert"]:
                    self.assertEqual(
                        assessment["alert"]["triggered_at"],
                        "2026-07-01T01:00:00+08:00",
                    )
                    self.assertTrue(assessment["alert"]["triggered_in_forecast"])

    def test_reservoir_alert_time_is_first_entry_into_highest_severity(self):
        assessment = assess_reservoir_window(
            {
                "simulation_time": "2026-07-01T00:00:00+08:00",
                "reservoir_level_m": 245.1,
            },
            [
                {
                    "valid_time": "2026-07-01T01:00:00+08:00",
                    "reservoir_level_m": 248.7,
                },
                {
                    "valid_time": "2026-07-01T02:00:00+08:00",
                    "reservoir_level_m": 248.92,
                },
                {
                    "valid_time": "2026-07-01T03:00:00+08:00",
                    "reservoir_level_m": 249.4,
                },
            ],
        )

        self.assertEqual(assessment["peak"]["valid_time"], "2026-07-01T03:00:00+08:00")
        self.assertEqual(assessment["alert"]["severity"], "critical")
        self.assertEqual(
            assessment["alert"]["triggered_at"],
            "2026-07-01T02:00:00+08:00",
        )
        self.assertIn("{triggered_at}超校核洪水位", assessment["alert"]["future_text_template"])

    def test_trigger_and_cnn_input_use_the_same_current_to_plus_24h_window(self):
        rows = _forecast_rows(30, {25: 240.0})
        for index, row in enumerate(rows):
            row["rainfall_mm"] = index + 0.125
        policy = self._policy(rows)

        self.assertEqual(policy.observe(rows[0]), [])
        request = policy.observe(rows[1])[0]

        self.assertEqual(request["time"], rows[1]["observed_at"])
        summary = request["payload"]["forecast_input"]
        trigger = request["payload"]["forecast_trigger"]
        self.assertEqual(summary["version"], 1)
        self.assertEqual(summary["simulation_time"], rows[1]["observed_at"])
        self.assertEqual(summary["window_start"], rows[1]["observed_at"])
        self.assertEqual(summary["window_end"], rows[25]["observed_at"])
        self.assertEqual(summary["forecast_point_count"], 25)
        self.assertEqual(len(summary["rainfall_series"]), 25)
        self.assertEqual(
            summary["rainfall_series"][0],
            {
                "time_h": 0,
                "valid_time": rows[1]["observed_at"],
                "rainfall_mm": 1.125,
            },
        )
        self.assertEqual(
            summary["rainfall_series"][-1],
            {
                "time_h": 24,
                "valid_time": rows[25]["observed_at"],
                "rainfall_mm": 25.125,
            },
        )
        self.assertEqual(trigger["trigger_type"], "forecast_window_peak")
        self.assertEqual(trigger["threshold_m3s"], 230.0)
        self.assertEqual(trigger["window_peak_total_flow_m3s"], 240.0)
        self.assertEqual(trigger["threshold_exceeded_at"], rows[25]["observed_at"])
        for boundary in summary["boundaries"].values():
            self.assertEqual(len(boundary["series"]), 25)
            self.assertEqual(boundary["series"][0]["time_h"], 0)
            self.assertEqual(boundary["series"][-1]["time_h"], 24)
            self.assertEqual(
                [point["flow_m3s"] for point in boundary["series"]],
                [row["total_flow_m3s"] / 4 for row in rows[1:26]],
            )
            self.assertEqual(
                {point["source"] for point in boundary["series"]},
                {"csv_forecast"},
            )

    def test_threshold_is_strictly_greater_than_230(self):
        rows = _forecast_rows(25, {24: 230.0})
        policy = self._policy(rows)

        self.assertEqual(policy.observe(rows[0]), [])
        self.assertEqual(policy.state, FloodForecastPolicy.NORMAL)
        self.assertEqual(policy.version, 0)

    def test_window_without_exceedance_does_not_trigger(self):
        rows = _forecast_rows(25)
        policy = self._policy(rows)

        self.assertEqual(policy.observe(rows[0]), [])
        self.assertFalse((self.temp_dir / "latest_forecast_input.json").exists())

    def test_fewer_than_25_remaining_points_does_not_trigger(self):
        rows = _forecast_rows(25, {24: 400.0})
        policy = self._policy(rows)

        self.assertEqual(policy.observe(rows[1]), [])
        self.assertEqual(policy.version, 0)

    def test_completed_forecast_does_not_trigger_again(self):
        rows = _forecast_rows(30, {24: 300.0, 25: 310.0})
        policy = self._policy(rows)
        request = policy.observe(rows[0])[0]
        input_id = request["source_id"]
        self.assertTrue(policy.mark_forecast_started(input_id))
        self.assertFalse(policy.mark_forecast_started(input_id))
        self.assertTrue(policy.mark_forecast_completed(input_id))

        later_events = [event for row in rows[1:] for event in policy.observe(row)]
        self.assertEqual(later_events, [])
        self.assertEqual(policy.version, 1)

    def test_every_rolling_step_retriggers_until_window_has_no_exceedance(self):
        rows = _forecast_rows(50, {24: 300.0})
        policy = self._policy(rows)
        initial = policy.observe(rows[0])[0]
        self.assertTrue(policy.mark_forecast_started(initial["source_id"]))
        self.assertTrue(policy.mark_forecast_completed(initial["source_id"]))

        for sequence in range(1, 25):
            request = policy.observe(rows[sequence], rolling=True)[0]
            trigger = request["payload"]["forecast_trigger"]
            summary = request["payload"]["forecast_input"]
            self.assertEqual(trigger["trigger_type"], "rolling_step")
            self.assertEqual(summary["window_start"], rows[sequence]["observed_at"])
            self.assertEqual(summary["window_end"], rows[sequence + 24]["observed_at"])
            self.assertTrue(policy.mark_forecast_started(request["source_id"]))
            self.assertTrue(policy.mark_forecast_completed(request["source_id"]))

        self.assertEqual(policy.observe(rows[25], rolling=True), [])
        self.assertEqual(policy.version, 25)
        self.assertEqual(policy.completed_version, 25)
        self.assertEqual(policy.state, FloodForecastPolicy.ACTIVE)

    def test_failed_forecast_can_retry_at_the_next_prediction_time(self):
        rows = _forecast_rows(26, {24: 300.0})
        policy = self._policy(rows)
        first = policy.observe(rows[0])[0]
        self.assertTrue(policy.mark_forecast_started(first["source_id"]))
        self.assertTrue(policy.mark_forecast_failed(first["source_id"]))

        retry = policy.observe(rows[1])[0]

        self.assertEqual(retry["time"], rows[1]["observed_at"])
        self.assertEqual(retry["payload"]["forecast_input"]["version"], 2)
        self.assertEqual(
            retry["payload"]["forecast_input"]["window_start"],
            rows[1]["observed_at"],
        )
        self.assertTrue(
            (self.temp_dir / "forecast_inputs" / "flood_20260101T0000" / "v002.json").exists()
        )

    def test_pending_request_is_coalesced(self):
        rows = _forecast_rows(26, {24: 300.0})
        policy = self._policy(rows)
        request = policy.observe(rows[0])[0]
        self.assertTrue(policy.mark_forecast_started(request["source_id"]))

        self.assertEqual(policy.observe(rows[1]), [])
        self.assertEqual(policy.version, 1)

    def _policy(self, rows):
        return FloodForecastPolicy(
            rows,
            forecast_input_dir=self.temp_dir / "forecast_inputs",
            latest_forecast_input_path=self.temp_dir / "latest_forecast_input.json",
        )


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
                [(61, 5.0, "forecast")],
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
            self.assertEqual(
                finished[0][1]["event_type"],
                "BoundaryFlowForecastAdvanced",
            )


class EventRuntimePlaybackControlTest(unittest.TestCase):
    def test_forecast_trigger_blocks_playback_at_the_current_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = WorkspaceManager(root / "workspaces", retention_count=3)
            manager.create()
            rows = _forecast_rows(26, {24: 300.0})
            source = BoundaryFlowPlaybackSource(
                CSV_PATH,
                root / "observations.jsonl",
            )
            source.rows = rows
            policy = FloodForecastPolicy(
                rows,
                forecast_input_dir=root / "forecast_inputs",
                latest_forecast_input_path=root / "latest.json",
            )
            runner = BoundaryFlowPlaybackRunner(
                BoundaryFlowPlayback(source, policy),
                interval_seconds=0,
            )
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._generation = 3
            runtime._playback_running = True
            runtime._playback_phase = "running"
            runtime._boundary_flow_runner = runner

            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                runner.play_generation(
                    generation=3,
                    is_running=runtime._is_playback_running,
                    publish_observation=runtime._publish_boundary_flow_observation,
                    publish_policy_event=runtime._publish_policy_event,
                    finish_sequence=runtime._finish_playback_sequence,
                    sleep_while_running=runtime._sleep_while_playback_running,
                )

            self.assertEqual(source.index, 1)
            self.assertFalse(runtime._playback_running)
            self.assertTrue(runtime._playback_processing)
            self.assertEqual(runtime._playback_phase, "processing")
            self.assertEqual(runtime.status()["policy_state"], "PENDING")
            self.assertEqual(
                [event[0]["event_type"] for event in runtime._event_queue],
                ["FloodForecastRequired"],
            )

    def test_processing_waits_for_inundation_followup_before_pausing(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            manager.create()
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._generation = 5
            runtime._playback_running = True
            forecast_event = {
                "event_id": "evt_forecast",
                "event_type": "FloodForecastRequired",
                "correlation_id": "flood_1",
                "payload": {"observation": {"observed_at": "2026-07-01T01:00:00+08:00"}},
            }
            inundation_event = {
                "event_id": "evt_inundation",
                "event_type": "InundationGenerated",
                "source_id": "forecast_1:boundary_1",
                "correlation_id": "flood_1",
                "title": "水动力模型生成预测淹没范围",
                "payload": {},
            }

            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                runtime._begin_event_processing(forecast_event)
                runtime._publish_inundation_event_once(inundation_event, 5)
                runtime._complete_event_processing(forecast_event, 5)
                self.assertTrue(runtime._playback_processing)
                self.assertFalse(runtime._playback_paused)

                runtime._complete_event_processing(inundation_event, 5)

            self.assertFalse(runtime._playback_processing)
            self.assertTrue(runtime._playback_paused)
            self.assertEqual(runtime._playback_phase, "paused")
            self.assertEqual(runtime.outputs[-1]["data"]["status"], "paused")

    def test_processing_pauses_when_forecast_does_not_generate_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            manager.create()
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._generation = 2
            runtime._playback_running = True
            event = {
                "event_id": "evt_failed",
                "event_type": "FloodForecastRequired",
                "correlation_id": "flood_failed",
                "payload": {},
            }

            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                runtime._begin_event_processing(event)
                runtime._complete_event_processing(event, 2)

            status = runtime.status()
            self.assertFalse(status["processing"])
            self.assertTrue(status["paused"])
            self.assertEqual(status["playback_phase"], "paused")
            self.assertIn("未生成有效", runtime.outputs[-1]["data"]["detail"])

    def test_processing_resumes_when_auto_pause_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            manager.create()
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._generation = 6
            runtime._playback_running = True
            event = {
                "event_id": "evt_forecast",
                "event_type": "FloodForecastRequired",
                "correlation_id": "flood_auto_continue",
                "payload": {},
            }

            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                setting = runtime.set_auto_pause(False)
                runtime._begin_event_processing(event)
                runtime._complete_event_processing(event, 6)

            status = runtime.status()
            self.assertFalse(setting["auto_pause_enabled"])
            self.assertFalse(status["processing"])
            self.assertFalse(status["paused"])
            self.assertTrue(status["running"])
            self.assertFalse(status["auto_pause_enabled"])
            self.assertEqual(status["playback_phase"], "running")
            self.assertIn("自动暂停已关闭", runtime.outputs[-1]["data"]["detail"])

    def test_auto_pause_setting_requires_a_boolean(self):
        runtime = EventRuntime(object())
        runtime._started = True

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            runtime.set_auto_pause("false")

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
        self.assertEqual(status["playback_phase"], "paused")
        self.assertEqual(runtime._generation, 7)
        self.assertEqual(list(runtime._event_queue), [(queued_event, 7)])
        self.assertEqual(runtime.outputs[-1]["data"]["status"], "paused")

        source_index = runtime._boundary_flow_runner.playback.source.index
        resumed = runtime.resume_playback(10)

        self.assertTrue(resumed["running"])
        self.assertFalse(resumed["paused"])
        self.assertEqual(resumed["playback_phase"], "running")
        self.assertEqual(runtime._generation, 7)
        self.assertEqual(runtime._boundary_flow_runner.playback.source.index, source_index)
        self.assertEqual(list(runtime._event_queue), [(queued_event, 7)])

    def test_step_advances_once_and_remains_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            manager.create()
            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                runtime = EventRuntime(object())
                runtime._started = True
                runtime._playback_paused = True
                source = runtime._boundary_flow_runner.playback.source
                before = source.index

                status = runtime.step_playback()

            self.assertTrue(status["stepped"])
            self.assertTrue(status["paused"])
            self.assertFalse(status["running"])
            self.assertFalse(status["forecast_triggered"])
            self.assertTrue(status["step_available"])
            self.assertEqual(source.index, before + 1)
            self.assertEqual(runtime.outputs[-1]["data"]["status"], "stepped")

    def test_step_is_rejected_while_forecast_is_pending(self):
        runtime = EventRuntime(object())
        runtime._started = True
        runtime._playback_paused = True
        runtime._boundary_flow_runner.playback.policy.state = "PENDING"
        source_index = runtime._boundary_flow_runner.playback.source.index

        with self.assertRaisesRegex(ValueError, "CNN 洪水预测尚未完成"):
            runtime.step_playback()

        self.assertEqual(
            runtime._boundary_flow_runner.playback.source.index,
            source_index,
        )

    def test_step_uses_rolling_policy_and_enqueues_next_forecast_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = WorkspaceManager(root / "workspaces", retention_count=3)
            manager.create()
            rows = _forecast_rows(27, {24: 300.0})
            source = BoundaryFlowPlaybackSource(
                CSV_PATH,
                root / "observations.jsonl",
            )
            source.rows = rows
            source.index = 1
            policy = FloodForecastPolicy(
                rows,
                forecast_input_dir=root / "forecast_inputs",
                latest_forecast_input_path=root / "latest.json",
            )
            initial = policy.observe(rows[0])[0]
            policy.mark_forecast_started(initial["source_id"])
            policy.mark_forecast_completed(initial["source_id"])

            with patch("server.events.runtime.WORKSPACES", manager), patch(
                "domains.flood.runtime.workspace.WORKSPACES", manager,
            ):
                runtime = EventRuntime(object())
                runtime._started = True
                runtime._playback_paused = True
                runtime._boundary_flow_runner = BoundaryFlowPlaybackRunner(
                    BoundaryFlowPlayback(source, policy),
                )

                status = runtime.step_playback()

            self.assertFalse(status["paused"])
            self.assertTrue(status["processing"])
            self.assertEqual(status["playback_phase"], "processing")
            self.assertTrue(status["forecast_triggered"])
            self.assertFalse(status["step_available"])
            self.assertEqual(status["policy_state"], "PENDING")
            self.assertEqual(status["forecast_version"], 2)
            queued_event, _ = runtime._event_queue[0]
            self.assertEqual(queued_event["event_type"], "FloodForecastRequired")
            self.assertEqual(
                queued_event["payload"]["forecast_trigger"]["trigger_type"],
                "rolling_step",
            )

    def test_restart_creates_new_workspace_and_waits_at_start(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=3)
            first = manager.create()["workspace_id"]
            runtime = EventRuntime(object())
            runtime._started = True
            runtime._playback_running = True
            runtime._generation = 4
            runtime._boundary_flow_runner.playback.source.index = 5

            with patch("server.events.runtime.WORKSPACES", manager):
                with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                    status = runtime.restart_playback(10)

            self.assertFalse(status["running"])
            self.assertFalse(status["paused"])
            self.assertEqual(status["status"], "reset")
            self.assertEqual(status["playback_phase"], "ready")
            self.assertEqual(status["speed_multiplier"], 10)
            self.assertEqual(runtime._boundary_flow_runner.playback.source.index, 0)
            self.assertEqual(status["workspace_id"], manager.active_id)
            self.assertNotEqual(status["workspace_id"], first)
            self.assertEqual(runtime.outputs[-1]["data"]["status"], "reset")
            first_manifest = json.loads(
                (manager.path(first) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_manifest["status"], "stopped")

    def test_runtime_status_outputs_include_stable_playback_phase(self):
        runtime = EventRuntime(object())
        runtime._started = True
        runtime._playback_running = True
        runtime._playback_phase = "running"

        runtime.set_playback_speed(10)

        output = runtime.outputs[-1]["data"]
        self.assertEqual(output["status"], "speed_changed")
        self.assertEqual(output["playback_phase"], "running")


class InundationMapEventTest(unittest.TestCase):
    def test_inundation_event_agent_sets_watershed_alert_without_impact_analysis(self):
        policy = ONTOLOGY.event_policies["InundationGenerated"]

        self.assertIn("ui_set_inundation_alert", policy.allowed_tools)
        self.assertNotIn("analyze_inundation_impacts", policy.allowed_tools)
        self.assertNotIn("analyze_inundation_impacts", policy.required_functions)
        self.assertIn(policy.automatic_map.tool, ONTOLOGY.presentation_tools)

    def test_inundation_event_prompt_is_built_from_ontology_policy(self):
        prompt = OntologyPromptBuilder(ONTOLOGY, FunctionRegistry()).build_event_prompt(
            "InundationGenerated",
            {"event_type": "InundationGenerated", "event_id": "evt_test"},
        )

        self.assertIn("forecast_cell_count>0", prompt)
        self.assertIn("必须调用一次 ui_set_inundation_alert", prompt)
        self.assertIn("不执行对象级影响分析", prompt)
        self.assertIn('"object_type": "HydrodynamicGridCell"', prompt)
        self.assertIn("只有用户在普通对话中明确请求时才可展示", prompt)

    def test_only_hydrodynamic_actions_reach_automatic_frontend_stream(self):
        event = {
            "type": "map_actions",
            "context": "预测淹没影响",
            "map_actions": [
                {"type": "show_hydrodynamic_mesh"},
                {"type": "apply_hydrodynamic_result", "filters": {"forecast_id": "latest"}},
                {"type": "set_watershed_inundation_alert", "active": True},
                {"type": "load_object", "object_type": "EvacuationRoute"},
                {"type": "clear_highlights"},
                {"type": "highlight_objects", "object_type": "EvacuationRoute"},
            ],
            "result_cards": [{"title": "受影响路线"}],
        }

        filtered = filter_event_map_event(
            event,
            ONTOLOGY.event_policies["InundationGenerated"].automatic_map,
        )

        self.assertEqual(
            [action["type"] for action in filtered["map_actions"]],
            [
                "show_hydrodynamic_mesh",
                "apply_hydrodynamic_result",
                "set_watershed_inundation_alert",
            ],
        )
        self.assertEqual(filtered["result_cards"], [])

    def test_targeted_impact_map_actions_are_suppressed(self):
        event = {
            "type": "map_actions",
            "context": "预测淹没影响",
            "map_actions": [
                {
                    "type": "load_object",
                    "object_type": "Facility",
                    "object_ids": ["facility_1"],
                    "replace_object_type": True,
                },
                {"type": "clear_highlights"},
                {
                    "type": "highlight_objects",
                    "object_type": "Facility",
                    "object_ids": ["facility_1"],
                },
            ],
        }

        filtered = filter_event_map_event(
            event,
            ONTOLOGY.event_policies["InundationGenerated"].automatic_map,
        )

        self.assertIsNone(filtered)

    def test_impact_only_map_event_is_suppressed(self):
        event = {
            "type": "map_actions",
            "map_actions": [{"type": "load_object", "object_type": "Facility"}],
        }

        self.assertIsNone(filter_event_map_event(
            event,
            ONTOLOGY.event_policies["InundationGenerated"].automatic_map,
        ))


def _forecast_rows(row_count, totals_by_sequence=None):
    totals_by_sequence = totals_by_sequence or {}
    start = datetime.fromisoformat("2026-01-01T00:00:00+08:00")
    labels = {
        "interval1": "区间1",
        "interval2": "区间2",
        "tonggu": "同古河",
        "upstream": "坝址",
    }
    rows = []
    for sequence in range(row_count):
        total = float(totals_by_sequence.get(sequence, 100.0))
        rows.append({
            "sequence": sequence,
            "observed_at": (start + timedelta(hours=sequence)).isoformat(),
            "rainfall_mm": 0.0,
            "reservoir_level_m": 245.1,
            "boundaries": {
                key: {"label": label, "flow_m3s": total / 4}
                for key, label in labels.items()
            },
            "total_flow_m3s": total,
        })
    return rows


if __name__ == "__main__":
    unittest.main()
