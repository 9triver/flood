from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Callable

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)

from .paths import MODEL_PATH
from .products import FloodProductDriver, verification_from_product


RUN_FORECAST = "run_forecast"
ForecastRunner = Callable[[dict, str], dict]


class HydrodynamicModelDriver(Driver):
    device_id = "flood:model:hydrodynamic:cnn-v2"
    operation_timeout_seconds = 30 * 60.0

    def __init__(
        self,
        products: FloodProductDriver,
        runner: ForecastRunner,
        *,
        implementation: str,
    ):
        self.products = products
        self.runner = runner
        self.implementation = implementation

    def bootstrap(self) -> None:
        self.kernel.interrupt(
            self.device_id,
            {"kind": "descriptor", "observed_at": self.kernel.clock()},
        )

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict) or raw.get("kind") != "descriptor":
            raise ValueError("model driver accepts only descriptor frames")
        yield NormalizedObservation(
            MODEL_PATH,
            {
                "kind": "model",
                "domain": "flood",
                "model_type": "hydrodynamic_forecast",
                "name": "珊瑚河 CNN 水动力模型",
                "implementation": self.implementation,
                "actions": [RUN_FORECAST],
                "input": {
                    "boundaries": ["upstream", "interval1", "interval2", "tonggu"],
                    "history_window_hours": 24,
                    "metric": "flow_m3s",
                },
                "output_product": "forecast",
            },
            float(raw["observed_at"]),
            self.device_id,
        )

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        if path != MODEL_PATH or action != RUN_FORECAST:
            return f"unsupported model action: {action} on {path}"
        stations = arguments.get("stations")
        if not isinstance(stations, dict) or not stations:
            return "arguments.stations must be a non-empty boundary map"
        for station_id, series in stations.items():
            if not isinstance(series, list) or not series:
                return f"arguments.stations[{station_id}] must be a non-empty series"
        if not isinstance(arguments.get("window_hours"), (int, float)):
            return "arguments.window_hours must be numeric"
        return None

    def dispatch(self, operation: Operation) -> None:
        product_id = f"forecast_{operation.operation_id.removeprefix('op_')}"
        threading.Thread(
            target=self._run,
            args=(operation, product_id),
            daemon=True,
            name=f"domain-os-mvp-{product_id}",
        ).start()

    def _run(self, operation: Operation, product_id: str) -> None:
        try:
            result = self.runner(operation.arguments, product_id)
        except Exception as exc:  # job failure is domain evidence
            self.products.publish(
                "forecast",
                product_id,
                operation_id=operation.operation_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                data={
                    "model_ref": MODEL_PATH,
                    "input_last_observed_at": operation.arguments.get(
                        "last_observed_at"
                    ),
                },
            )
            return
        self.products.publish(
            "forecast",
            product_id,
            operation_id=operation.operation_id,
            data={
                "model_ref": MODEL_PATH,
                "input_last_observed_at": operation.arguments.get(
                    "last_observed_at"
                ),
                "input_revision": operation.arguments.get("input_revision"),
                "input_summary": {
                    "total_m3s": operation.arguments.get("total_m3s"),
                    "window_hours": operation.arguments.get("window_hours"),
                    "boundaries": sorted(operation.arguments["stations"]),
                },
                "result": result,
            },
        )

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return verification_from_product(operation, evidence, "forecast")


class SyntheticHydrodynamicRunner:
    """Fast surrogate used when the deployable CNN is not configured."""

    def __call__(self, arguments: dict, product_id: str) -> dict:
        peak_total = float(arguments.get("total_m3s") or 0.0)
        excess = max(0.0, peak_total - 230.0)
        max_depth = round(min(5.0, excess / 80.0), 3)
        return {
            "forecast_id": product_id,
            "model": "synthetic-hydrodynamic-surrogate",
            "is_surrogate": True,
            "horizon_hours": 24,
            "time_step_hours": 0.5,
            "stats": {
                "peak_boundary_total_m3s": peak_total,
                "max_depth_m": max_depth,
                "wet_cell_count": int(excess * 20),
                "inundation_expected": excess > 0,
            },
        }


class ExistingCnnRunner:
    """Adapter for the repository's deployable CNN package."""

    def __init__(self, artifact_root: Path):
        self.artifact_root = Path(artifact_root)

    def __call__(self, arguments: dict, product_id: str) -> dict:
        from domains.flood.dos_forecast import real_cnn_runner

        return real_cnn_runner(
            arguments,
            self.artifact_root / product_id,
        )
