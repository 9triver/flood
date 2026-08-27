from __future__ import annotations

import threading

import pytest

from domain_os_mvp import (
    CapabilityError,
    FrozenResourceError,
    InvalidActionError,
    Kernel,
    OperationConflictError,
    PreconditionError,
)
from domain_os_mvp.examples.station import (
    INTERVAL_PATH,
    LEVEL_PATH,
    SET_SAMPLING_INTERVAL,
    STATION_BASE,
    TelemetryStationDriver,
    spawn_water_level_monitor,
)


class ManualClock:
    def __init__(self, value: float = 1_800_000_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 1.0) -> float:
        self.value += seconds
        return self.value


def mount_station(database, clock, *, timeout_seconds=10):
    kernel = Kernel(database, clock=clock)
    driver = TelemetryStationDriver(timeout_seconds=timeout_seconds)
    kernel.mount(STATION_BASE, driver)
    return kernel, driver


def seed_station(kernel, driver, clock):
    kernel.interrupt(
        driver.device_id,
        {
            "observed_at": clock(),
            "level_m": 1.8,
            "sampling_interval_seconds": 300,
        },
    )
    kernel.pump()


def test_privileged_closed_loop_reuses_pending_and_requires_fresh_evidence(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    events = []
    spawn_water_level_monitor(kernel, capability.token, sink=events)
    seed_station(kernel, driver, clock)

    kernel.interrupt(
        driver.device_id,
        {"observed_at": clock.advance(), "level_m": 3.5},
    )
    kernel.pump()
    operation = kernel.operations({"awaiting_approval"})[0]
    assert events[-1].operation_id == operation.operation_id
    assert events[-1].reused is False

    kernel.interrupt(
        driver.device_id,
        {"observed_at": clock.advance(), "level_m": 3.6},
    )
    kernel.pump()
    assert events[-1].operation_id == operation.operation_id
    assert events[-1].reused is True
    assert len(kernel.operations()) == 1

    result = kernel.approve(
        operation.operation_id,
        approved_by="operator",
        decision=True,
        reason="warning level exceeded",
    )
    assert result.state == "dispatched"
    assert driver.dispatch_count == 1

    # The pre-dispatch interval observation exists in world state, but cannot
    # confirm this operation because its journal sequence is before dispatch.
    kernel.pump()
    assert kernel.operation(operation.operation_id).state == "dispatched"

    # A post-dispatch observation with the old value is inconclusive.
    kernel.interrupt(
        driver.device_id,
        {
            "observed_at": clock.advance(),
            "sampling_interval_seconds": 300,
        },
    )
    kernel.pump()
    assert kernel.operation(operation.operation_id).state == "dispatched"

    kernel.interrupt(
        driver.device_id,
        {
            "observed_at": clock.advance(),
            "sampling_interval_seconds": 30,
        },
    )
    kernel.pump()
    assert kernel.operation(operation.operation_id).state == "committed"
    assert kernel.read(INTERVAL_PATH).value == 30
    kernel.close()


def test_rejected_syscalls_do_not_enter_the_journal(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    seed_station(kernel, driver, clock)
    revision = kernel.read(INTERVAL_PATH).revision

    before = kernel.store.journal_count
    with pytest.raises(CapabilityError):
        kernel.act(
            "not-a-capability",
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": 30},
            expected_revision=revision,
        )
    assert kernel.store.journal_count == before

    with pytest.raises(PreconditionError):
        kernel.act(
            capability.token,
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": 30},
            expected_revision=revision - 1,
        )
    assert kernel.store.journal_count == before

    with pytest.raises(InvalidActionError):
        kernel.act(
            capability.token,
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": 0},
            expected_revision=revision,
        )
    assert kernel.store.journal_count == before
    kernel.close()


def test_conflicting_operation_is_rejected_before_journaling(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    seed_station(kernel, driver, clock)
    revision = kernel.read(INTERVAL_PATH).revision
    kernel.act(
        capability.token,
        INTERVAL_PATH,
        SET_SAMPLING_INTERVAL,
        {"seconds": 30},
        expected_revision=revision,
    )
    before = kernel.store.journal_count

    with pytest.raises(OperationConflictError):
        kernel.act(
            capability.token,
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": 60},
            expected_revision=revision,
        )
    assert kernel.store.journal_count == before
    kernel.close()


def test_unknown_freezes_resource_and_survives_restart(tmp_path):
    database = tmp_path / "kernel.sqlite"
    clock = ManualClock()
    kernel, driver = mount_station(database, clock, timeout_seconds=5)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    seed_station(kernel, driver, clock)
    operation = kernel.act(
        capability.token,
        INTERVAL_PATH,
        SET_SAMPLING_INTERVAL,
        {"seconds": 30},
        expected_revision=kernel.read(INTERVAL_PATH).revision,
    )
    kernel.approve(
        operation.operation_id,
        approved_by="operator",
        decision=True,
    )

    clock.advance(6)
    kernel.pump()
    assert kernel.operation(operation.operation_id).state == "unknown"
    kernel.close()

    recovered, recovered_driver = mount_station(database, clock, timeout_seconds=5)
    assert recovered.read(INTERVAL_PATH).value == 300
    assert recovered.operation(operation.operation_id).state == "unknown"
    assert recovered_driver.dispatch_count == 0
    with pytest.raises(FrozenResourceError):
        recovered.act(
            capability.token,
            INTERVAL_PATH,
            SET_SAMPLING_INTERVAL,
            {"seconds": 30},
            expected_revision=recovered.read(INTERVAL_PATH).revision,
        )
    recovered.close()


def test_dispatched_operation_is_reconciled_but_not_redispatched_after_restart(tmp_path):
    database = tmp_path / "kernel.sqlite"
    clock = ManualClock()
    kernel, driver = mount_station(database, clock, timeout_seconds=20)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    seed_station(kernel, driver, clock)
    result = kernel.act(
        capability.token,
        INTERVAL_PATH,
        SET_SAMPLING_INTERVAL,
        {"seconds": 30},
        expected_revision=kernel.read(INTERVAL_PATH).revision,
    )
    kernel.approve(
        result.operation_id,
        approved_by="operator",
        decision=True,
    )
    assert driver.dispatch_count == 1
    kernel.close()

    recovered, recovered_driver = mount_station(database, clock, timeout_seconds=20)
    assert recovered.operation(result.operation_id).state == "dispatched"
    assert recovered_driver.dispatch_count == 0

    recovered.interrupt(
        recovered_driver.device_id,
        {
            "observed_at": clock.advance(),
            "sampling_interval_seconds": 30,
        },
    )
    recovered.pump()
    assert recovered.operation(result.operation_id).state == "committed"
    assert recovered_driver.dispatch_count == 0
    recovered.close()


def test_late_observation_is_audited_without_rewinding_current_state(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    seed_station(kernel, driver, clock)
    current = kernel.read(LEVEL_PATH)

    kernel.interrupt(
        driver.device_id,
        {"observed_at": clock() - 60, "level_m": 0.5},
    )
    kernel.pump()

    assert kernel.read(LEVEL_PATH) == current
    assert [item.value for item in kernel.history(LEVEL_PATH)] == [1.8, 0.5]
    kernel.close()


def test_all_projections_can_be_rebuilt_from_the_journal(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    capability = kernel.grant(
        "monitor",
        STATION_BASE,
        {SET_SAMPLING_INTERVAL},
    )
    seed_station(kernel, driver, clock)
    interval = kernel.read(INTERVAL_PATH)
    result = kernel.act(
        capability.token,
        INTERVAL_PATH,
        SET_SAMPLING_INTERVAL,
        {"seconds": 30},
        expected_revision=interval.revision,
    )
    journal_count = kernel.store.journal_count

    kernel.store.rebuild_projections()

    assert kernel.read(INTERVAL_PATH) == interval
    assert kernel.operation(result.operation_id).state == "awaiting_approval"
    reused = kernel.act(
        capability.token,
        INTERVAL_PATH,
        SET_SAMPLING_INTERVAL,
        {"seconds": 30},
        expected_revision=interval.revision,
    )
    assert reused.operation_id == result.operation_id
    assert reused.reused is True
    assert kernel.store.journal_count == journal_count
    kernel.close()


def test_watch_waits_for_revision_to_advance(tmp_path):
    clock = ManualClock()
    kernel, driver = mount_station(tmp_path / "kernel.sqlite", clock)
    seed_station(kernel, driver, clock)
    current = kernel.read(LEVEL_PATH)
    result = []

    waiter = threading.Thread(
        target=lambda: result.append(
            kernel.watch(
                LEVEL_PATH,
                after_revision=current.revision,
                timeout=2,
            )
        )
    )
    waiter.start()
    kernel.interrupt(
        driver.device_id,
        {"observed_at": clock.advance(), "level_m": 2.0},
    )
    kernel.pump()
    waiter.join(timeout=2)

    assert not waiter.is_alive()
    assert result[0].value == 2.0
    kernel.close()
