from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from domain_os_mvp import Kernel

from .agent_api import FloodDomainClient
from .assets import DEFAULT_OBJECTS_DIR, FloodAssetDriver
from .hydrodynamic import (
    RUN_FORECAST,
    ForecastRunner,
    HydrodynamicModelDriver,
    SyntheticHydrodynamicRunner,
)
from .paths import (
    ASSESSMENTS_BASE,
    ASSETS_BASE,
    MODEL_PATH,
    PRODUCTS_BASE,
    ROUTING_PATH,
    SCENARIO_PATH,
    SENSORS_BASE,
)
from .products import PUBLISH_ASSESSMENT, FloodProductDriver
from .resources import DescriptorDriver
from .routing import (
    PLAN_ROUTE,
    DirectRouteRunner,
    RouteRunner,
    RoutingServiceDriver,
)
from .scenario import spawn_flood_emergency_scenario
from .telemetry import MqttHydrologyDriver


@dataclass
class FloodWorld:
    kernel: Kernel
    assets: FloodAssetDriver
    telemetry: MqttHydrologyDriver
    products: FloodProductDriver
    hydrodynamic: HydrodynamicModelDriver
    routing: RoutingServiceDriver
    scenario: DescriptorDriver
    capabilities: dict[str, str]
    scenario_events: list = field(default_factory=list)
    _thread: threading.Thread | None = None

    def agent_client(self) -> FloodDomainClient:
        return FloodDomainClient(
            self.kernel,
            {
                "forecast": self.capabilities["agent_forecast"],
                "routing": self.capabilities["agent_routing"],
                "assessment": self.capabilities["agent_assessment"],
            },
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self.kernel.run,
            daemon=True,
            name="domain-os-mvp-flood-world",
        )
        self._thread.start()

    def stop(self) -> None:
        self.kernel.stop()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def close(self) -> None:
        self.stop()
        self.kernel.close()


def build_flood_world(
    database: str | Path = ":memory:",
    *,
    objects_dir: Path = DEFAULT_OBJECTS_DIR,
    clock: Callable[[], float] = time.time,
    forecast_runner: ForecastRunner | None = None,
    forecast_implementation: str = "synthetic-surrogate",
    route_runner: RouteRunner | None = None,
    route_implementation: str = "offline-direct-surrogate",
    trigger_total_m3s: float = 230.0,
) -> FloodWorld:
    kernel = Kernel(database, clock=clock)
    products = FloodProductDriver()
    assets = FloodAssetDriver(objects_dir)
    telemetry = MqttHydrologyDriver()
    hydrodynamic = HydrodynamicModelDriver(
        products,
        forecast_runner or SyntheticHydrodynamicRunner(),
        implementation=forecast_implementation,
    )
    routing = RoutingServiceDriver(
        products,
        route_runner or DirectRouteRunner(),
        implementation=route_implementation,
    )
    scenario = DescriptorDriver(
        "flood:scenario:emergency",
        SCENARIO_PATH,
        {
            "kind": "scenario",
            "domain": "flood",
            "name": "珊瑚河防洪应急",
            "watches": ["boundary flow telemetry"],
            "uses": [MODEL_PATH, ROUTING_PATH, ASSETS_BASE],
            "produces": ["forecast", "route", "assessment"],
            "trigger_total_m3s": float(trigger_total_m3s),
        },
    )

    kernel.mount(ASSETS_BASE, assets)
    kernel.mount(SENSORS_BASE, telemetry)
    kernel.mount(PRODUCTS_BASE, products)
    kernel.mount(MODEL_PATH, hydrodynamic)
    kernel.mount(ROUTING_PATH, routing)
    kernel.mount(SCENARIO_PATH, scenario)

    capabilities = {
        "scenario_forecast": kernel.grant(
            "flood-emergency-scenario",
            MODEL_PATH,
            {RUN_FORECAST},
        ).token,
        "agent_forecast": kernel.grant(
            "flood-agent",
            MODEL_PATH,
            {RUN_FORECAST},
        ).token,
        "agent_routing": kernel.grant(
            "flood-agent",
            ROUTING_PATH,
            {PLAN_ROUTE},
        ).token,
        "agent_assessment": kernel.grant(
            "flood-agent",
            ASSESSMENTS_BASE,
            {PUBLISH_ASSESSMENT},
        ).token,
    }

    events = []
    spawn_flood_emergency_scenario(
        kernel,
        capabilities["scenario_forecast"],
        trigger_total_m3s=trigger_total_m3s,
        sink=events,
    )

    bootstrapped = False
    for path, bootstrap in (
        (ASSETS_BASE, assets.bootstrap),
        (SENSORS_BASE + "/stations", telemetry.bootstrap),
        (MODEL_PATH, hydrodynamic.bootstrap),
        (ROUTING_PATH, routing.bootstrap),
        (SCENARIO_PATH, scenario.bootstrap),
    ):
        if kernel.read(path) is None:
            bootstrap()
            bootstrapped = True
    if bootstrapped:
        kernel.pump()

    return FloodWorld(
        kernel=kernel,
        assets=assets,
        telemetry=telemetry,
        products=products,
        hydrodynamic=hydrodynamic,
        routing=routing,
        scenario=scenario,
        capabilities=capabilities,
        scenario_events=events,
    )
