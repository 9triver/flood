"""End-to-end check for the independent Domain OS MVP."""

from __future__ import annotations

import tempfile
from pathlib import Path

from domain_os_mvp import Kernel
from domain_os_mvp.examples.station import (
    INTERVAL_PATH,
    LEVEL_PATH,
    SET_SAMPLING_INTERVAL,
    STATION_BASE,
    TelemetryStationDriver,
    spawn_water_level_monitor,
)


class Clock:
    def __init__(self, value: float = 1_800_000_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 1.0) -> float:
        self.value += seconds
        return self.value


def main() -> int:
    clock = Clock()
    with tempfile.TemporaryDirectory(prefix="domain-os-mvp-") as directory:
        database = Path(directory) / "kernel.sqlite"
        kernel = Kernel(database, clock=clock)
        driver = TelemetryStationDriver(timeout_seconds=10)
        kernel.mount(STATION_BASE, driver)
        capability = kernel.grant(
            "water-level-monitor",
            STATION_BASE,
            {SET_SAMPLING_INTERVAL},
        )
        process_events = []
        spawn_water_level_monitor(kernel, capability.token, sink=process_events)

        print("1. ingest normal telemetry")
        kernel.interrupt(
            driver.device_id,
            {
                "observed_at": clock(),
                "level_m": 1.8,
                "sampling_interval_seconds": 300,
            },
        )
        kernel.pump()
        print(
            f"   level={kernel.read(LEVEL_PATH).value}m, "
            f"interval={kernel.read(INTERVAL_PATH).value}s"
        )

        print("2. cross warning level and request a privileged operation")
        kernel.interrupt(
            driver.device_id,
            {"observed_at": clock.advance(), "level_m": 3.5},
        )
        kernel.pump()
        operation = kernel.operations({"awaiting_approval"})[0]
        print(f"   {operation.operation_id} -> {operation.state}")

        print("3. repeat the decision; the in-flight operation is reused")
        kernel.interrupt(
            driver.device_id,
            {"observed_at": clock.advance(), "level_m": 3.6},
        )
        kernel.pump()
        assert process_events[-1].reused is True
        assert len(kernel.operations()) == 1
        print(f"   reused={process_events[-1].reused}")

        print("4. approve and dispatch once")
        result = kernel.approve(
            operation.operation_id,
            approved_by="operator-chen",
            decision=True,
            reason="warning level exceeded",
        )
        assert result.state == "dispatched"
        assert driver.dispatch_count == 1
        print(f"   state={result.state}, dispatch_count={driver.dispatch_count}")

        print("5. accept only post-dispatch telemetry as outcome evidence")
        kernel.interrupt(
            driver.device_id,
            {
                "observed_at": clock.advance(),
                "sampling_interval_seconds": driver.commanded_interval,
            },
        )
        kernel.pump()
        committed = kernel.operation(operation.operation_id)
        assert committed.state == "committed"
        print(f"   state={committed.state}")

        print("6. audit the complete fact chain")
        for record in kernel.store.journal():
            event = record.payload.get("event", record.payload.get("path", ""))
            print(f"   #{record.seq:02d} {record.kind:<11} {event}")
        kernel.close()

    print("OK: Domain OS MVP closed loop verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
