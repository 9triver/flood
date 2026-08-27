"""dos MCP server hosting the full flood business loop (stdio transport).

Mounted world (all on one kernel daemon):

    /hydro/shanhu/stations/808J1510/...     demo station (level + sampling)
    /hydro/shanhu/stations/{boundary}/flow_m3s   four boundary stations
    /hydro/shanhu/views/level_status        derived
    /hydro/shanhu/forecasts/...             CNN compute device (fake by default)
    /hydro/shanhu/impacts/...               impact compute device (fake)

Standing processes: level-monitor (privileged act on warning),
forecast-trigger (stateless, mirror-driven), impact-auto (standard sweep).

A feeder thread plays the devices: the storm's 24h boundary-flow history is
published at startup (crossing the 230 m³/s threshold at the end), then the
station keeps reporting every couple of seconds; every minute a new
boundary hour arrives so fresh forecasts keep being produced.

Env:
    DOS_JOURNAL    durable journal path (default: temp dir)
    DOS_ACT_GRANT  0 to refuse minting act capabilities at open_session
    DOS_FORECAST   "real" to run the real CNN_V2 (slow; needs torch+weights)

Run:  uv run python scripts/dos_mcp_server.py
Smoke: uv run python scripts/check_dos_mcp.py
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from dos import JsonlSink, Journal, mount_assessments, spawn_observer_watchdog
from dos.gateway import DosGateway
from dos.mcp_server import build_mcp_server
from dos.mqtt import InMemoryMqttBus
from domains.flood.dos_forecast import BOUNDARIES, mount_forecast, spawn_forecast_trigger
from domains.flood.dos_impact import mount_impact, spawn_impact_auto
from domains.flood.dos_instance import STATION, build_mqtt_kernel, spawn_monitor

PREFIX = "water"
BASE_TS = 1_800_000_000.0
ASSESSMENTS = "/hydro/shanhu/assessments"


def telemetry(message_id: str, observed_at: float, metrics: dict) -> bytes:
    return json.dumps(
        {
            "message_id": message_id,
            "observed_at": datetime.fromtimestamp(observed_at, tz=timezone.utc).isoformat(),
            "metrics": {name: {"value": value, "unit": "m"} for name, value in metrics.items()},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def fake_forecast_runner(args: dict, target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    (target / "depth_series.npy").write_bytes(b"fake")
    return {
        "stats": {"wet_cells": 4200 + len(str(args)) % 97, "max_depth_m": 3.1},
        "artifacts": {"depth_series": str(target / "depth_series.npy"), "max_depth_csv": str(target / "max_depth.csv")},
        "model": {"model_name": "FAKE"},
    }


def fake_impact_runner(args: dict, target: Path, forecast_meta: dict) -> dict:
    return {
        "summary": {"affected": 3, "by_risk": {"high": 1, "medium": 1, "low": 1}},
        "highlights": [{"id": "bridge-1", "risk": "high"}],
        "artifacts": {"geojson": str(target / "impact.geojson")},
    }


def main() -> int:
    journal_path = os.environ.get("DOS_JOURNAL") or str(
        Path(tempfile.mkdtemp(prefix="dos-mcp-")) / "journal.jsonl"
    )
    real_model = os.environ.get("DOS_FORECAST", "") == "real"

    bus = InMemoryMqttBus()
    stations = {STATION, *BOUNDARIES}
    kernel = build_mqtt_kernel(
        bus, stations, topic_prefix=PREFIX, journal=Journal(clock=time.time, sink=JsonlSink(journal_path))
    )
    if real_model:
        from scripts.check_dos_forecast import real_forecast_runner

        forecast_runner = real_forecast_runner
    else:
        forecast_runner = fake_forecast_runner
    mount_forecast(kernel, forecast_runner)
    mount_impact(kernel, fake_impact_runner)
    mount_assessments(kernel, ASSESSMENTS)

    monitor_cap = kernel.grant(f"/hydro/shanhu/stations/{STATION}", {"set_sampling_interval"}, "server-boot")
    forecast_cap = kernel.grant("/hydro/shanhu/forecasts", {"run_forecast"}, "server-boot")
    impact_cap = kernel.grant("/hydro/shanhu/impacts", {"analyze_impact"}, "server-boot")
    assessment_cap = kernel.grant(ASSESSMENTS, {"file_assessment"}, "server-boot")
    kernel_events: list[str] = []
    spawn_monitor(kernel, monitor_cap.token, sink=kernel_events)
    spawn_forecast_trigger(kernel, forecast_cap.token, sink=kernel_events)
    spawn_impact_auto(kernel, impact_cap.token, targets=[{"type": "bridge", "id": "bridge-1"}, {"type": "point", "id": "village-a"}], sink=kernel_events)

    stop = threading.Event()
    gateway = DosGateway(kernel, allow_act_grant=os.environ.get("DOS_ACT_GRANT", "1") == "1")

    # 看守进程：常驻 agent 失联即立案
    spawn_observer_watchdog(
        kernel, assessment_cap.token, gateway,
        expected={"duty-agent": 120.0},
        check_every=15.0, alert_cooldown=300.0,
        assessments_base=ASSESSMENTS, sink=kernel_events,
    )

    def publish(topic: str, payload: bytes) -> None:
        bus.inject(topic, payload)

    def feed_history() -> None:
        """The storm's 24h boundary-flow history, threshold crossed at the end."""
        for hour in range(25):
            for boundary in BOUNDARIES:
                flow = 60.0 if hour >= 24 else 10.0
                publish(
                    f"{PREFIX}/stations/{boundary}/telemetry",
                    telemetry(f"boot-h{hour}-{boundary}", BASE_TS + hour * 3600.0, {"flow_m3s": flow}),
                )

    def feeder() -> None:
        # the station plays a real device: it reports its own configuration
        # and obeys downlink commands (sampling interval changes)
        def on_command(topic: str, payload: bytes) -> None:
            command = json.loads(payload.decode("utf-8"))
            if command.get("operation") == "set_sampling_interval":
                state["interval"] = int(command["seconds"])

        bus.subscribe(f"{PREFIX}/stations/{STATION}/commands", on_command)
        feed_history()
        publish(
            f"{PREFIX}/stations/{STATION}/telemetry",
            telemetry(f"boot-{STATION}", BASE_TS + 24 * 3600.0, {"level_m": 3.5, "sampling_interval_seconds": state["interval"]}),
        )
        tick = 0
        while not stop.is_set():
            stop.wait(2.0)
            if stop.is_set():
                return
            tick += 1
            level = 3.5 + (0.1 if tick % 2 else 0.0)
            publish(
                f"{PREFIX}/stations/{STATION}/telemetry",
                telemetry(f"live-{STATION}-{tick}", time.time(), {"level_m": level, "sampling_interval_seconds": state["interval"]}),
            )
            if tick % 30 == 0:  # every minute: a new boundary hour arrives
                hour = 25 + tick // 30
                for boundary in BOUNDARIES:
                    publish(
                        f"{PREFIX}/stations/{boundary}/telemetry",
                        telemetry(f"live-h{hour}-{boundary}", BASE_TS + hour * 3600.0, {"flow_m3s": 60.0 + tick % 7}),
                    )

    state = {"interval": 600}
    threading.Thread(target=feeder, daemon=True, name="dos-feeder").start()
    pump_thread = threading.Thread(target=kernel.run, kwargs={"idle_seconds": 0.05}, daemon=True, name="dos-pump")
    pump_thread.start()

    def duty_agent() -> None:
        """常驻值班研判 agent（演示）：进程是条件反射，它是第一个理解反射结果的人。

        无状态纪律：不记"上次处理到哪"——读自己立过的最新 situation 评估，
        若其引用的预测与当前 latest 一致即不重复立案。"""
        session = gateway.open_session(
            "duty-agent",
            read_scopes=("/hydro/shanhu",),
            act_prefix=ASSESSMENTS,
            act_actions=["file_assessment"],
        )
        sid = session.session_id
        forecast_latest = "/hydro/shanhu/forecasts/latest"
        situation_latest = f"{ASSESSMENTS}/by-kind/situation/latest"
        while not stop.is_set():
            try:
                current = gateway.try_read(sid, forecast_latest)
                generation = current["generation"] if current else -1
                gateway.wait_for_change(sid, [forecast_latest], {forecast_latest: generation}, timeout=10.0)
                snap = gateway.try_read(sid, forecast_latest)
                if snap is None or not snap["value"].get("id"):
                    continue
                forecast_id = snap["value"]["id"]
                prior = gateway.try_read(sid, situation_latest)
                if prior is not None and prior["value"].get("refs", {}).get("forecast_id") == forecast_id:
                    continue  # 已对这份预测立过案
                meta = gateway.try_read(sid, f"/hydro/shanhu/forecasts/{forecast_id}") or {"value": {}}
                impact = gateway.try_read(sid, "/hydro/shanhu/impacts/latest") or {"value": {}}
                stats = (meta.get("value") or {}).get("stats") or {}
                summary = (impact.get("value") or {}).get("id", "评估进行中")
                gateway.act(
                    sid, f"{ASSESSMENTS}/latest", "file_assessment",
                    {
                        "kind": "situation",
                        "title": f"态势研判：{forecast_id}",
                        "content": {
                            "summary": f"边界总量越阈，模型湿润单元 {stats.get('wet_cells')}、最大水深 {stats.get('max_depth_m')}m；标准影响评估：{summary}。建议关注高危对象。",
                            "model": (meta.get("value") or {}).get("model", {}),
                        },
                        "refs": {"forecast_id": forecast_id, "impact_id": (impact.get("value") or {}).get("id")},
                        "author": "duty-agent",
                    },
                )
            except Exception as exc:  # noqa: BLE001 — 值守循环不许死
                print(f"[dos-mcp-server] duty-agent cycle error: {exc}", file=sys.stderr)
                stop.wait(5.0)

    threading.Thread(target=duty_agent, daemon=True, name="dos-duty-agent").start()

    def shutdown() -> None:
        stop.set()
        kernel.stop()

    atexit.register(shutdown)

    server = build_mcp_server(gateway, name="dos-flood")
    print(
        f"[dos-mcp-server] journal={journal_path} model={'CNN_V2' if real_model else 'fake'}\n"
        f"[dos-mcp-server] stations={sorted(stations)} processes=[level-monitor, forecast-trigger, impact-auto, observer-watchdog]\n"
        f"[dos-mcp-server] standing agents=[duty-agent] world paths: /hydro/shanhu/{{stations,views,forecasts,impacts,assessments}}",
        file=sys.stderr,
    )
    server.run("stdio")
    shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
