"""Run one complete flood domain world on the independent MVP kernel."""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

from domain_os_mvp.domains.flood.hydrodynamic import ExistingCnnRunner
from domain_os_mvp.domains.flood.paths import ASSETS_BASE
from domain_os_mvp.domains.flood.routing import AmapRouteRunner
from domain_os_mvp.domains.flood.telemetry import mqtt_payload
from domain_os_mvp.domains.flood.world import build_flood_world
from domains.flood.runtime.boundary_flow import load_boundary_flow_rows


def wait_until(world, predicate, label: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        world.kernel.pump()
        result = predicate()
        if result is not None:
            return result
        time.sleep(0.01)
    raise TimeoutError(f"timed out waiting for {label}")


def load_local_env() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-amap", action="store_true")
    parser.add_argument("--real-cnn", action="store_true")
    args = parser.parse_args()
    load_local_env()

    with tempfile.TemporaryDirectory(prefix="flood-domain-world-") as directory:
        root = Path(directory)
        route_runner = None
        route_implementation = "offline-direct-surrogate"
        if args.real_amap:
            route_runner = AmapRouteRunner(os.environ.get("AMAP_WEB_SERVICE_KEY", ""))
            route_implementation = "amap-web-service"
        forecast_runner = None
        forecast_implementation = "synthetic-surrogate"
        if args.real_cnn:
            forecast_runner = ExistingCnnRunner(root / "forecasts")
            forecast_implementation = "repository-cnn-v2"

        world = build_flood_world(
            root / "kernel.sqlite",
            route_runner=route_runner,
            route_implementation=route_implementation,
            forecast_runner=forecast_runner,
            forecast_implementation=forecast_implementation,
        )
        agent = world.agent_client()

        catalog = agent.read(ASSETS_BASE).value
        print(
            f"1. world mounted: {catalog['object_count']} GIS objects / "
            f"{len(catalog['counts'])} types"
        )

        row = next(
            item
            for item in load_boundary_flow_rows()
            if float(item["total_flow_m3s"]) > 230.0
        )
        for station_id, boundary in row["boundaries"].items():
            world.telemetry.ingest(
                f"water/stations/{station_id}/telemetry",
                mqtt_payload(
                    station_id,
                    observed_at=row["observed_at"],
                    flow_m3s=boundary["flow_m3s"],
                    message_id=f"demo-{row['sequence']}-{station_id}",
                ),
            )
        world.kernel.pump()
        forecast = wait_until(
            world,
            lambda: agent.latest_product("forecast"),
            "forecast product",
            timeout=1200 if args.real_cnn else 30,
        )
        stats = forecast.value["data"]["result"]["stats"]
        print(
            f"2. MQTT -> forecast {forecast.value['product_id']}: "
            f"max_depth={stats['max_depth_m']}m"
        )

        units = agent.list_asset_refs("EvacuationUnit")
        sites = agent.list_asset_refs("EvacuationSite")
        route_operation = agent.plan_route(
            units[0],
            sites[0],
            profile="foot",
            forecast_ref=forecast.path,
        )
        route = wait_until(
            world,
            lambda: (
                agent.latest_product("route")
                if agent.operation(route_operation.operation_id).terminal
                else None
            ),
            "route product",
        )
        route_data = route.value["data"]["route"]
        print(
            f"3. Agent -> route {route.value['product_id']}: "
            f"{route_data['distance_m']}m"
        )

        assessment_operation = agent.publish_assessment(
            basis_refs=[forecast.path, route.path],
            conclusions=[
                {
                    "subject_ref": units[0],
                    "risk": "needs_review",
                    "summary": "预测和路线产品已形成，等待值班人员研判。",
                }
            ],
            recommendations=[{"action": "review_evacuation_readiness"}],
            agent={"runtime": "demo-agent", "model": "deterministic-check"},
        )
        assessment = wait_until(
            world,
            lambda: (
                agent.latest_product("assessment")
                if agent.operation(assessment_operation.operation_id).terminal
                else None
            ),
            "assessment product",
        )
        print(f"4. Agent assessment published: {assessment.value['product_id']}")
        print(f"5. auditable journal records: {world.kernel.store.journal_count}")
        world.close()

    print("OK: flood domain world runs on the six Domain OS syscalls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
