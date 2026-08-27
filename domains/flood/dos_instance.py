"""Flood domain instance on the dos kernel — the first mounted world.

Everything water-specific lives here; the kernel (``dos/``) contains no
water concepts.  The instance can be built on either driver:

- ``build_kernel``      — in-process simulator (fast demos, unit tests)
- ``build_mqtt_kernel`` — real MQTT telemetry stations (dos/mqtt.py)

Both mount the same namespace vocabulary, aligned with the MQTT wire
format proven by the first-generation kernel:

    /hydro/shanhu/stations/808J1510/level_m                    m
    /hydro/shanhu/stations/808J1510/sampling_interval_seconds  s
    /hydro/shanhu/views/level_status                           derived
"""

from __future__ import annotations

import time
from typing import Iterable, Optional

from dos import Driver, Kernel, ProcessSpec
from dos.devices import PendingTxn


def _default_clock():
    return time.time

STATION = "808J1510"
BASE = f"/hydro/shanhu/stations/{STATION}"
LEVEL_PATH = f"{BASE}/level_m"
INTERVAL_PATH = f"{BASE}/sampling_interval_seconds"
STATUS_PATH = "/hydro/shanhu/views/level_status"
SET_INTERVAL = "set_sampling_interval"
WATCH_LEVEL = 2.4  # m
WARNING_LEVEL = 3.2  # m
FAST_INTERVAL = 60  # s


def _level_status(ns):
    snap = ns.try_read(LEVEL_PATH)
    if snap is None:
        return "unknown"
    level = snap.value
    if level >= WARNING_LEVEL:
        return "warning"
    if level >= WATCH_LEVEL:
        return "watch"
    return "normal"


def _mount(kernel: Kernel, driver: Driver) -> None:
    kernel.mount("/hydro/shanhu", driver)
    kernel.derive(STATUS_PATH, (LEVEL_PATH,), _level_status)


def build_kernel(clock=None, journal=None) -> Kernel:
    kernel = Kernel(journal=journal, clock=clock or _default_clock())
    _mount(kernel, TelemetryStationDriver())
    return kernel


def build_mqtt_kernel(transport, station_ids: Iterable[str], topic_prefix: str = "water", clock=None, journal=None) -> Kernel:
    from dos.mqtt import MqttTelemetryDriver

    kernel = Kernel(journal=journal, clock=clock or _default_clock())
    _mount(kernel, MqttTelemetryDriver(transport, station_ids, base="/hydro/shanhu", topic_prefix=topic_prefix))
    return kernel


class TelemetryStationDriver(Driver):
    """In-process station simulator.  Observation discipline: telemetry is
    committed per frame, configuration only on change."""

    privileged_actions = frozenset({SET_INTERVAL})
    default_txn_timeout = 10.0

    def __init__(self, device_id: str = f"station-{STATION}"):
        self.device_id = device_id
        self.dispatched: list[PendingTxn] = []
        self.sampling_interval = 600  # s, the device's real config
        self._last_emitted_interval = None

    # top half: raw frame {"level_m": float, "ts": float}
    def normalize(self, raw: object) -> Iterable[tuple]:
        observed_at = float(raw.get("ts") or time.time())
        yield LEVEL_PATH, float(raw["level_m"]), observed_at
        if self.sampling_interval != self._last_emitted_interval:
            self._last_emitted_interval = self.sampling_interval
            yield INTERVAL_PATH, self.sampling_interval, observed_at

    # downlink
    def dispatch(self, txn: PendingTxn) -> None:
        self.dispatched.append(txn)
        if txn.action == SET_INTERVAL:
            # the device applies the new config on its next uplink
            self.sampling_interval = int(txn.args["seconds"])

    # fsck rule: the kernel filters out evidence older than the dispatch
    def verify(self, txn: PendingTxn, read) -> str:
        if txn.action != SET_INTERVAL:
            return "pending"
        snap = read(INTERVAL_PATH)
        if snap is None:
            return "pending"
        return "committed" if snap.value == txn.args["seconds"] else "pending"


def spawn_monitor(kernel: Kernel, cap_token: str, sink: Optional[list] = None) -> None:
    """Supervised process: watch the level; on warning, tighten sampling."""
    events = sink if sink is not None else []

    def handler(ctx):
        status = ctx.read(STATUS_PATH).value
        events.append(f"status={status}")
        interval_snap = ctx.read(INTERVAL_PATH)
        if status == "warning" and interval_snap.value != FAST_INTERVAL:
            result = ctx.act(
                cap_token,
                INTERVAL_PATH,
                SET_INTERVAL,
                {"seconds": FAST_INTERVAL},
                expect={INTERVAL_PATH: interval_snap.value},
            )
            events.append(f"act->{result.state}" + ("(reused)" if result.reused else ""))

    kernel.spawn(
        ProcessSpec(
            name="level-monitor",
            watches=(LEVEL_PATH,),
            handler=handler,
            priority=5,
            description="水位监视：警戒时申请加密采样",
        )
    )
