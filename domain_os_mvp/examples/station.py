from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    ProcessSpec,
    Verification,
)


STATION_ID = "808J1510"
STATION_BASE = f"/hydro/stations/{STATION_ID}"
LEVEL_PATH = f"{STATION_BASE}/level_m"
INTERVAL_PATH = f"{STATION_BASE}/sampling_interval_seconds"
SET_SAMPLING_INTERVAL = "set_sampling_interval"


class TelemetryStationDriver(Driver):
    device_id = f"station:{STATION_ID}"
    privileged_actions = frozenset({SET_SAMPLING_INTERVAL})

    def __init__(self, *, timeout_seconds: float = 30.0):
        self.operation_timeout_seconds = float(timeout_seconds)
        self.commanded_interval: int | None = None
        self.dispatch_count = 0

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict):
            raise ValueError("telemetry frame must be an object")
        if "observed_at" not in raw:
            raise ValueError("telemetry frame requires observed_at")
        observed_at = float(raw["observed_at"])
        emitted = False
        if "level_m" in raw:
            level = float(raw["level_m"])
            if not math.isfinite(level) or level < 0:
                raise ValueError("level_m must be a non-negative finite number")
            emitted = True
            yield NormalizedObservation(
                LEVEL_PATH,
                level,
                observed_at,
                self.device_id,
            )
        if "sampling_interval_seconds" in raw:
            interval = int(raw["sampling_interval_seconds"])
            if interval <= 0:
                raise ValueError("sampling_interval_seconds must be positive")
            emitted = True
            yield NormalizedObservation(
                INTERVAL_PATH,
                interval,
                observed_at,
                self.device_id,
            )
        if not emitted:
            raise ValueError("telemetry frame contains no supported observation")

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        if path != INTERVAL_PATH:
            return f"action target must be {INTERVAL_PATH}"
        if action != SET_SAMPLING_INTERVAL:
            return f"unsupported action: {action}"
        value = arguments.get("seconds")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return "arguments.seconds must be a positive integer"
        return None

    def dispatch(self, operation: Operation) -> None:
        self.dispatch_count += 1
        self.commanded_interval = int(operation.arguments["seconds"])

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        expected = int(operation.arguments["seconds"])
        for observation in reversed(evidence):
            if observation.path != INTERVAL_PATH:
                continue
            if int(observation.value) == expected:
                return Verification.committed()
        return Verification.pending()


def spawn_water_level_monitor(
    kernel,
    capability_token: str,
    *,
    warning_level_m: float = 3.0,
    fast_interval_seconds: int = 30,
    sink: list | None = None,
) -> None:
    events = sink if sink is not None else []

    def handler(context) -> None:
        level = context.read(LEVEL_PATH)
        interval = context.read(INTERVAL_PATH)
        if level is None or interval is None:
            return
        if float(level.value) < warning_level_m:
            return
        if int(interval.value) <= fast_interval_seconds:
            return
        result = context.act(
            capability_token,
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": fast_interval_seconds},
            expected_revision=interval.revision,
        )
        events.append(result)

    kernel.spawn(
        ProcessSpec(
            name="water-level-monitor",
            watches=(LEVEL_PATH,),
            handler=handler,
        )
    )
