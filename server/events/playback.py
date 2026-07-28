from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from domains.flood.runtime.boundary_flow import (
    BoundaryFlowPlayback,
    BoundaryFlowPlaybackSource,
)


FORECAST_ATTENTION_STATES = frozenset({"PENDING", "ACTIVE", "RECEDING"})
PLAYBACK_SPEEDS = frozenset({1.0, 2.0, 5.0, 10.0, 20.0})


def adaptive_playback_speed(observation: dict[str, Any],
                            policy_state: str) -> tuple[float, str, str]:
    if policy_state in FORECAST_ATTENTION_STATES:
        return (
            5.0,
            "forecast",
            "洪水预测规则已触发，演进速率自动降为 5×，便于观察模型计算与事件研判。",
        )
    if float(observation.get("rainfall_mm") or 0) > 0:
        return (
            10.0,
            "rainfall",
            "监测到降雨过程开始，演进速率自动降为 10×，便于观察边界流量上涨。",
        )
    return 20.0, "baseline", "无雨基础过程按 20× 速率快速演进。"


class BoundaryFlowPlaybackRunner:
    def __init__(self, playback: BoundaryFlowPlayback | None = None,
                 interval_seconds: float = 5.0):
        self.playback = playback or BoundaryFlowPlayback()
        self.base_interval_seconds = interval_seconds
        self._speed_multiplier = 20.0
        self._speed_lock = threading.Lock()

    @property
    def interval_seconds(self) -> float:
        with self._speed_lock:
            return self.base_interval_seconds / self._speed_multiplier

    @property
    def speed_multiplier(self) -> float:
        with self._speed_lock:
            return self._speed_multiplier

    def set_speed(self, multiplier: float) -> float:
        value = float(multiplier)
        if value not in PLAYBACK_SPEEDS:
            raise ValueError("playback speed must be one of 1, 2, 5, 10, 20")
        with self._speed_lock:
            self._speed_multiplier = value
        return value

    def reset(self) -> None:
        self.playback.reset()

    def replace_source(self, csv_path: Path) -> None:
        self.playback = BoundaryFlowPlayback(BoundaryFlowPlaybackSource(csv_path))

    def mark_forecast_started(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_started(forecast_input_id)

    def mark_forecast_completed(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_completed(forecast_input_id)

    def mark_forecast_failed(self, forecast_input_id: str) -> bool:
        return self.playback.mark_forecast_failed(forecast_input_id)

    def step(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        return self.playback.next_events(
            rolling=True,
            trigger_source="manual_step",
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.playback.status(),
            "speed_multiplier": self.speed_multiplier,
            "interval_seconds": self.interval_seconds,
        }

    def run_forever(self, *,
                    wait_until_running: Callable[[], int],
                    is_running: Callable[[int], bool],
                    publish_observation: Callable[[dict[str, Any]], None],
                    publish_policy_event: Callable[[dict[str, Any]], None],
                    finish_sequence: Callable[[int, dict[str, Any] | None], None],
                    sleep_while_running: Callable[[float, int], None]) -> None:
        while True:
            generation = wait_until_running()
            time.sleep(1.0)
            if not is_running(generation):
                continue
            self.play_generation(
                generation=generation,
                is_running=is_running,
                publish_observation=publish_observation,
                publish_policy_event=publish_policy_event,
                finish_sequence=finish_sequence,
                sleep_while_running=sleep_while_running,
            )

    def play_generation(self, *, generation: int,
                        is_running: Callable[[int], bool],
                        publish_observation: Callable[[dict[str, Any]], None],
                        publish_policy_event: Callable[[dict[str, Any]], None],
                        finish_sequence: Callable[[int, dict[str, Any] | None], None],
                        sleep_while_running: Callable[[float, int], None]) -> None:
        last_observation: dict[str, Any] | None = None
        while is_running(generation):
            observation_event, policy_events = self.playback.next_events(
                rolling=True,
                trigger_source="automatic_playback",
            )
            if observation_event is None:
                finish_sequence(generation, last_observation)
                return
            if not is_running(generation):
                return
            last_observation = observation_event
            publish_observation(observation_event)
            for event in policy_events:
                publish_policy_event(event)
            sleep_while_running(self.interval_seconds, generation)
