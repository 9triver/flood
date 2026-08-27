"""Flood domain instance on the dos kernel — the first mounted world.

Everything water-specific lives here; the kernel (``dos/``) contains no
water concepts.  This instance mounts one simulated telemetry station
(珊瑚河 808J1510) and runs a supervised monitor process that derives
warning views and, when the level reaches the warning mark, requests a
privileged sampling-interval change through the act() syscall.

Paths mounted under ``/hydro/shanhu``:

    stations/808J1510/level            m, latest telemetry
    stations/808J1510/sample_interval  s, device configuration (committed
                                       only when it actually changes)
    views/level_status                 derived: normal | watch | warning
"""

from __future__ import annotations

from typing import Iterable, Optional

from dos import Driver, Kernel, ProcessSpec
from dos.devices import PendingTxn

STATION = "808J1510"
BASE = f"/hydro/shanhu/stations/{STATION}"
WATCH_LEVEL = 2.4  # m
WARNING_LEVEL = 3.2  # m
FAST_INTERVAL = 60  # s


class TelemetryStationDriver(Driver):
    """A water-level station.  In real deployments ``interrupt`` is fed by
    MQTT; here a simulator feeds it.  Observation discipline: telemetry is
    committed per frame, configuration only on change."""

    privileged_actions = frozenset({"set_interval"})
    default_txn_timeout = 10.0

    def __init__(self, device_id: str = f"station-{STATION}"):
        self.device_id = device_id
        self.dispatched: list[PendingTxn] = []
        self.sample_interval = 600  # s, the device's real config
        self._last_emitted_interval = None

    # top half: raw MQTT-ish frame {"level_m": float, "ts": float}
    def normalize(self, raw: object) -> Iterable[tuple[str, object]]:
        yield f"{BASE}/level", float(raw["level_m"])
        if self.sample_interval != self._last_emitted_interval:
            self._last_emitted_interval = self.sample_interval
            yield f"{BASE}/sample_interval", self.sample_interval

    # downlink
    def dispatch(self, txn: PendingTxn) -> None:
        self.dispatched.append(txn)
        if txn.action == "set_interval":
            # the device applies the new config on its next uplink
            self.sample_interval = int(txn.args["interval_s"])

    # fsck rule: a config txn commits when telemetry evidence *newer than
    # the dispatch* reports the new interval (the kernel filters stale
    # evidence before we see it)
    def verify(self, txn: PendingTxn, read) -> str:
        if txn.action != "set_interval":
            return "pending"
        snap = read(f"{BASE}/sample_interval")
        if snap is None:
            return "pending"
        return "committed" if snap.value == int(txn.args["interval_s"]) else "pending"


def build_kernel(clock=None) -> Kernel:
    kernel = Kernel(clock=clock)

    driver = TelemetryStationDriver()
    kernel.mount("/hydro/shanhu", driver)

    # derived view: level status — page cache over raw telemetry
    def level_status(ns):
        snap = ns.try_read(f"{BASE}/level")
        if snap is None:
            return "unknown"
        level = snap.value
        if level >= WARNING_LEVEL:
            return "warning"
        if level >= WATCH_LEVEL:
            return "watch"
        return "normal"

    kernel.derive("/hydro/shanhu/views/level_status", (f"{BASE}/level",), level_status)
    return kernel


def spawn_monitor(kernel: Kernel, cap_token: str, sink: Optional[list] = None) -> None:
    """Supervised process: watch the level; on warning, tighten sampling."""
    events = sink if sink is not None else []

    def handler(ctx):
        status = ctx.read("/hydro/shanhu/views/level_status").value
        events.append(f"status={status}")
        interval_snap = ctx.read(f"{BASE}/sample_interval")
        if status == "warning" and interval_snap.value != FAST_INTERVAL:
            result = ctx.act(
                cap_token,
                f"{BASE}/sample_interval",
                "set_interval",
                {"interval_s": FAST_INTERVAL},
                expect={f"{BASE}/sample_interval": interval_snap.value},
            )
            events.append(f"act->{result.state}" + ("(reused)" if result.reused else ""))

    kernel.spawn(
        ProcessSpec(
            name="level-monitor",
            watches=(f"{BASE}/level",),
            handler=handler,
            priority=5,
            description="水位监视：警戒时申请加密采样",
        )
    )
