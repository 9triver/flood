"""Closed-loop demo: 感知—判断—控制—反馈 on the dos kernel.

Simulates 珊瑚河 station 808J1510: telemetry arrives, the monitor process
wakes, the level crosses the warning threshold, the process asks (via
act(), with human approval) to tighten the sampling interval, the device
confirms on its next uplink, and fsck commits the transaction.

Run: uv run python scripts/check_dos_flood.py
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from dos import Kernel
from domains.flood.dos_instance import (
    FAST_INTERVAL,
    INTERVAL_PATH,
    STATUS_PATH,
    TelemetryStationDriver,
    build_kernel,
    spawn_monitor,
)


def banner(text: str) -> None:
    print(f"\n== {text} ==")


def main() -> int:
    kernel = build_kernel(clock=lambda: time.time())
    driver = kernel.drivers["station-808J1510"]
    events: list[str] = []

    cap = kernel.grant(f"/hydro/shanhu/stations", {"set_sampling_interval"}, granted_by="demo-boot", description="水位站采样配置操作")
    spawn_monitor(kernel, cap.token, events)

    banner("1. 感知：正常水位遥测")
    kernel.interrupt(driver.device_id, {"level_m": 1.8, "ts": time.time()})
    kernel.pump()
    print(f"  level={kernel.read('/hydro/shanhu/stations/808J1510/level_m').value}m  status={kernel.read(STATUS_PATH).value}")
    assert kernel.read(STATUS_PATH).value == "normal"

    banner("2. 判断：水位越过警戒，监视进程唤醒并申请加密采样（特权 → 审批）")
    kernel.interrupt(driver.device_id, {"level_m": 3.5, "ts": time.time()})
    kernel.pump()
    print(f"  events: {events}")
    pending = kernel.consistency.pending()
    assert len(pending) == 1 and pending[0].state == "awaiting_approval"
    txn_id = pending[0].txn_id
    print(f"  txn {txn_id} awaiting approval (action=set_sampling_interval → {FAST_INTERVAL}s)")

    banner("2b. 幂等：审批挂起期间又一帧遥测，监视进程重试 act → 复用同一事务")
    kernel.interrupt(driver.device_id, {"level_m": 3.55, "ts": time.time()})
    kernel.pump()
    print(f"  events: {events[-2:]}")
    assert len(kernel.consistency.pending()) == 1, "重试不得产生第二个事务"
    assert kernel.txn(txn_id).state == "awaiting_approval"

    banner("3. 控制：人工批准，命令下发")
    result = kernel.approve(txn_id, approved_by="值班员-陈", decision=True, reason="汛情加密观测")
    print(f"  state={result.state}")
    assert result.state == "dispatched"

    banner("4. 反馈：下一帧遥测携带新采样间隔，fsck 提交事务")
    kernel.interrupt(driver.device_id, {"level_m": 3.6, "ts": time.time()})
    kernel.pump()
    txn = kernel.txn(txn_id)
    print(f"  txn state={txn.state}  sampling_interval={kernel.read(INTERVAL_PATH).value}s")
    assert txn.state == "committed"

    banner("5. 审计：journal 全程可回放")
    for record in kernel.journal.replay():
        label = record.payload.get("event") or record.kind
        detail = {k: v for k, v in record.payload.items() if k != "event"}
        print(f"  #{record.seq:>3} {record.kind:<11} {label} {detail}")

    print("\nOK: dos kernel closed loop verified (感知→判断→控制→反馈)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
