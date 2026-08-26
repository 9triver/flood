"""Flood forecast vertical slice built on the domain OS runtime."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from domain_os import (
    Capability,
    CapabilityRisk,
    Command,
    CommandResult,
    CommandState,
    DerivedProduct,
    DomainPolicy,
    DomainRuntime,
    DomainRuntimeError,
    DomainStore,
    DriverHealth,
    InfrastructureDriver,
    Intent,
    Observation,
    ObservationQuality,
    ObservationSink,
    Resource,
    RiskBasedPolicy,
    new_id,
    utc_now,
)

from .runtime.boundary_flow import (
    BOUNDARIES,
    FORECAST_TRIGGER_TOTAL_M3S,
    FORECAST_WINDOW_HOURS,
    FORECAST_WINDOW_POINT_COUNT,
    load_boundary_flow_rows,
)
from .runtime.cnn_v2 import run_cnn_v2_forecast


DOMAIN_ID = "water.flood"
EVOLUTION_DRIVER_ID = "water.infrastructure.boundary-evolution"
FORECAST_DRIVER_ID = "water.infrastructure.flood-model"
EVOLUTION_RESOURCE_ID = "water.evolution-source/boundary-flow"
WATERSHED_RESOURCE_ID = "water.watershed/shanhu"
RESERVOIR_RESOURCE_ID = "water.reservoir/longtan"
FORECAST_MODEL_RESOURCE_ID = "water.model/flood-cnn-v2"
RUN_FLOOD_FORECAST = "water.flood.run-forecast"
FORECAST_INPUT_PRODUCT = "water.flood.forecast-input"
FORECAST_PRODUCT = "water.flood.forecast"
FORECAST_REQUIRED_EVENT = "water.flood.forecast.required"
FORECAST_GENERATED_EVENT = "water.flood.forecast.generated"
FORECAST_FAILED_EVENT = "water.flood.forecast.failed"

BOUNDARY_RESOURCE_IDS = {
    key: f"water.boundary/{key}"
    for key in BOUNDARIES
}

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = (
    PROJECT_DIR / "local" / "runtime" / "domain-os" / DOMAIN_ID / "products"
)

ForecastRunner = Callable[[dict[str, Any], Path], dict[str, Any]]


def run_domain_cnn_forecast(
    forecast_input: dict[str, Any],
    target_depth_path: Path,
) -> dict[str, Any]:
    return run_cnn_v2_forecast(
        forecast_input,
        target_depth_path,
        working_dir=target_depth_path.parent / "_work",
    )


class BoundaryFlowEvolutionDriver(InfrastructureDriver):
    """Replays scenario rows while reporting only current facts as observations."""

    driver_id = EVOLUTION_DRIVER_ID

    def __init__(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        run_id: str | None = None,
        source_ref: str = "boundary-flow-scenario",
    ) -> None:
        self.connected = False
        self._sink: ObservationSink | None = None
        self.rows: tuple[dict[str, Any], ...] = ()
        self.run_id = ""
        self.source_ref = ""
        self.index = 0
        self.reset(rows, run_id=run_id, source_ref=source_ref)

    @property
    def has_next(self) -> bool:
        return self.index < len(self.rows)

    def reset(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        run_id: str | None = None,
        source_ref: str = "boundary-flow-scenario",
    ) -> None:
        if not rows:
            raise ValueError("boundary evolution rows must not be empty")
        self.rows = tuple(dict(row) for row in rows)
        self.run_id = str(run_id or f"evolution-{uuid4().hex[:12]}")
        self.source_ref = str(source_ref or "boundary-flow-scenario")
        self.index = 0

    async def start(self, sink: ObservationSink) -> None:
        self._sink = sink
        self.connected = True

    async def stop(self) -> None:
        self.connected = False
        self._sink = None

    def health(self) -> DriverHealth:
        return DriverHealth(
            driver_id=self.driver_id,
            connected=self.connected,
            checked_at=utc_now(),
            details={
                "run_id": self.run_id,
                "source_ref": self.source_ref,
                "row_count": len(self.rows),
                "next_sequence": self.index,
            },
        )

    async def execute(self, command: Command) -> CommandResult:
        return CommandResult(
            accepted=False,
            error=f"evolution source does not execute capability {command.intent.capability_id}",
        )

    async def advance(self) -> dict[str, Any] | None:
        if not self.connected or self._sink is None:
            raise RuntimeError("boundary evolution driver is not started")
        if not self.has_next:
            return None

        sequence = self.index
        row = self.rows[sequence]
        observed_at = _timestamp(row.get("observed_at"))
        received_at = utc_now()
        attributes = {
            "evolution_run_id": self.run_id,
            "projection_epoch": self.run_id,
            "source_kind": "scenario",
            "sequence": sequence,
        }

        observations: list[Observation] = []
        for boundary_key in BOUNDARIES:
            boundary = (row.get("boundaries") or {}).get(boundary_key) or {}
            observations.append(self._observation(
                sequence=sequence,
                resource_id=BOUNDARY_RESOURCE_IDS[boundary_key],
                metric="flow_m3s",
                value=float(boundary.get("flow_m3s") or 0),
                unit="m3/s",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ))

        observations.extend((
            self._observation(
                sequence=sequence,
                resource_id=WATERSHED_RESOURCE_ID,
                metric="rainfall_mm",
                value=float(row.get("rainfall_mm") or 0),
                unit="mm",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ),
            self._observation(
                sequence=sequence,
                resource_id=WATERSHED_RESOURCE_ID,
                metric="boundary_total_flow_m3s",
                value=_total_flow(row),
                unit="m3/s",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ),
            self._observation(
                sequence=sequence,
                resource_id=RESERVOIR_RESOURCE_ID,
                metric="inflow_m3s",
                value=float(row.get("reservoir_inflow_m3s") or 0),
                unit="m3/s",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ),
            self._observation(
                sequence=sequence,
                resource_id=RESERVOIR_RESOURCE_ID,
                metric="release_m3s",
                value=float(row.get("reservoir_release_m3s") or 0),
                unit="m3/s",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ),
            self._observation(
                sequence=sequence,
                resource_id=RESERVOIR_RESOURCE_ID,
                metric="water_level_m",
                value=float(row.get("reservoir_level_m") or 0),
                unit="m",
                observed_at=observed_at,
                received_at=received_at,
                attributes=attributes,
            ),
        ))

        # The sequence marker is last so domain rules see a complete current snapshot.
        observations.append(self._observation(
            sequence=sequence,
            resource_id=EVOLUTION_RESOURCE_ID,
            metric="sequence",
            value=sequence,
            unit=None,
            observed_at=observed_at,
            received_at=received_at,
            attributes=attributes,
        ))

        for observation in observations:
            await self._sink(observation)
        self.index += 1
        return dict(row)

    def window(self, sequence: int) -> tuple[dict[str, Any], ...]:
        if sequence < 0:
            return ()
        return self.rows[sequence:sequence + FORECAST_WINDOW_POINT_COUNT]

    def sequence_observation_id(self, sequence: int) -> str:
        return self._observation_id(
            sequence,
            EVOLUTION_RESOURCE_ID,
            "sequence",
        )

    def _observation(
        self,
        *,
        sequence: int,
        resource_id: str,
        metric: str,
        value: Any,
        unit: str | None,
        observed_at: datetime,
        received_at: datetime,
        attributes: dict[str, Any],
    ) -> Observation:
        return Observation(
            observation_id=self._observation_id(sequence, resource_id, metric),
            resource_id=resource_id,
            metric=metric,
            value=value,
            unit=unit,
            observed_at=observed_at,
            received_at=received_at,
            quality=ObservationQuality.GOOD,
            sequence=sequence,
            source_ref=self.source_ref,
            attributes=attributes,
        )

    def _observation_id(self, sequence: int, resource_id: str, metric: str) -> str:
        return f"{self.run_id}:{sequence}:{resource_id}:{metric}"


class FloodForecastModelDriver(InfrastructureDriver):
    """Executes the CNN model and returns a versioned forecast product."""

    driver_id = FORECAST_DRIVER_ID

    def __init__(
        self,
        runtime: DomainRuntime,
        *,
        runner: ForecastRunner = run_domain_cnn_forecast,
        artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    ) -> None:
        self.runtime = runtime
        self.runner = runner
        self.artifact_root = Path(artifact_root)
        self.connected = False

    async def start(self, sink: ObservationSink) -> None:
        self.connected = True

    async def stop(self) -> None:
        self.connected = False

    def health(self) -> DriverHealth:
        return DriverHealth(
            driver_id=self.driver_id,
            connected=self.connected,
            checked_at=utc_now(),
            details={"artifact_root": str(self.artifact_root)},
        )

    async def execute(self, command: Command) -> CommandResult:
        if not self.connected:
            return CommandResult(accepted=False, error="forecast model driver is not started")
        if command.intent.capability_id != RUN_FLOOD_FORECAST:
            return CommandResult(
                accepted=False,
                error=f"unsupported capability: {command.intent.capability_id}",
            )
        input_product_id = str(
            command.intent.arguments.get("input_product_id") or ""
        ).strip()
        if not input_product_id:
            return CommandResult(
                accepted=False,
                error="input_product_id is required",
            )
        try:
            input_product = self.runtime.product(input_product_id)
        except DomainRuntimeError as exc:
            return CommandResult(accepted=False, error=str(exc))
        if input_product.product_type != FORECAST_INPUT_PRODUCT:
            return CommandResult(
                accepted=False,
                error=f"unsupported forecast input product: {input_product.product_type}",
            )

        output_product_id = _forecast_product_id(input_product.product_id)
        try:
            existing_product = self.runtime.product(output_product_id)
        except DomainRuntimeError:
            existing_product = None
        if existing_product is not None:
            if (
                existing_product.product_type != FORECAST_PRODUCT
                or existing_product.input_refs != (input_product.product_id,)
            ):
                return CommandResult(
                    accepted=False,
                    error=f"forecast product id is already in use: {output_product_id}",
                )
            return CommandResult(
                accepted=True,
                external_id=f"cnn-{command.command_id}",
                output={
                    "product_id": existing_product.product_id,
                    "model_name": str(existing_product.data.get("model_name") or ""),
                    "reused": True,
                },
                products=(existing_product,),
            )

        output_dir = self.artifact_root / _safe_component(output_product_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        depth_path = output_dir / "max_depth.csv"
        try:
            result = await asyncio.to_thread(
                self.runner,
                dict(input_product.data),
                depth_path,
            )
        except Exception as exc:
            return CommandResult(
                accepted=False,
                error=f"forecast model execution failed: {exc}",
            )
        if not isinstance(result, dict):
            return CommandResult(
                accepted=False,
                error="forecast model returned an invalid result",
            )
        if result.get("error"):
            return CommandResult(
                accepted=False,
                error=str(result.get("error")),
                output={
                    "detail": str(result.get("detail") or ""),
                    "stderr": str(result.get("stderr") or "")[-2000:],
                },
            )

        positive_depths = result.pop("_positive_depths", None)
        forecast_cell_count = int(
            result.get("forecast_cell_count")
            or result.get("flooded_count")
            or (len(positive_depths) if isinstance(positive_depths, dict) else 0)
        )
        artifacts = _forecast_artifacts(depth_path)
        product = DerivedProduct(
            product_id=output_product_id,
            product_type=FORECAST_PRODUCT,
            subject_id=WATERSHED_RESOURCE_ID,
            producer_id=FORECAST_MODEL_RESOURCE_ID,
            generated_at=utc_now(),
            valid_from=input_product.valid_from,
            valid_to=input_product.valid_to,
            input_refs=(input_product.product_id,),
            data={
                "status": str(result.get("status") or "completed"),
                "model_name": str(result.get("model_name") or "FLOOD_CNN_V2"),
                "model_description": str(result.get("model_description") or ""),
                "forecast_input_id": input_product.product_id,
                "forecast_cell_count": forecast_cell_count,
                "inundated_area_km2": float(result.get("inundated_area_km2") or 0),
                "max_depth_m": float(result.get("max_depth_m") or 0),
                "mean_depth_m": float(result.get("mean_depth_m") or 0),
                "time_step_count": int(result.get("time_step_count") or 0),
                "time_steps_h": list(result.get("time_steps_h") or []),
                "device": str(result.get("device") or ""),
                "timings_ms": dict(result.get("timings_ms") or {}),
            },
            artifacts=artifacts,
            correlation_id=input_product.correlation_id,
            causation_id=command.command_id,
        )
        return CommandResult(
            accepted=True,
            external_id=f"cnn-{command.command_id}",
            output={
                "product_id": product.product_id,
                "model_name": product.data["model_name"],
            },
            products=(product,),
        )


class FloodForecastCoordinator:
    """Turns completed scenario snapshots into governed forecast commands."""

    def __init__(
        self,
        runtime: DomainRuntime,
        evolution_driver: BoundaryFlowEvolutionDriver,
        *,
        total_trigger_m3s: float = FORECAST_TRIGGER_TOTAL_M3S,
    ) -> None:
        self.runtime = runtime
        self.evolution_driver = evolution_driver
        self.total_trigger_m3s = float(total_trigger_m3s)
        self._episode_id: str | None = None
        self._version = 0
        self._dispose = runtime.subscribe(
            self._on_projection_updated,
            event_type="domain.projection.updated",
        )

    def close(self) -> None:
        self._dispose()

    def reset(self) -> None:
        self._episode_id = None
        self._version = 0

    def will_trigger(self, sequence: int) -> bool:
        selected = self.evolution_driver.window(sequence)
        if len(selected) != FORECAST_WINDOW_POINT_COUNT:
            return False
        window_start = _timestamp(selected[0].get("observed_at"))
        window_end = _timestamp(selected[-1].get("observed_at"))
        if window_end - window_start != timedelta(hours=FORECAST_WINDOW_HOURS):
            raise ValueError("forecast input window must span exactly 24 hours")
        return any(
            _total_flow(row) > self.total_trigger_m3s
            for row in selected
        )

    async def _on_projection_updated(self, event) -> None:
        if (
            event.subject_id != EVOLUTION_RESOURCE_ID
            or event.data.get("metric") != "sequence"
        ):
            return
        sequence = int(event.data["value"])
        product_id = _forecast_input_product_id(
            self.evolution_driver.run_id,
            sequence,
        )
        try:
            input_product = self.runtime.product(product_id)
        except DomainRuntimeError:
            input_product = self._build_input_product(sequence)
            if input_product is None:
                self._episode_id = None
                return
            await self.runtime.record_product(input_product)

        await self.runtime.publish_event(
            FORECAST_REQUIRED_EVENT,
            WATERSHED_RESOURCE_ID,
            {
                "input_product_id": input_product.product_id,
                "trigger": dict(input_product.data.get("forecast_trigger") or {}),
            },
            correlation_id=input_product.correlation_id,
            causation_id=input_product.causation_id,
        )
        command = await self.runtime.submit_intent(Intent(
            intent_id=new_id("intent"),
            actor_id="water.rule.forecast-trigger",
            resource_id=FORECAST_MODEL_RESOURCE_ID,
            capability_id=RUN_FLOOD_FORECAST,
            arguments={"input_product_id": input_product.product_id},
            requested_at=utc_now(),
            rationale="Deterministic boundary-flow forecast threshold exceeded",
            correlation_id=input_product.correlation_id,
        ))
        if command.state is CommandState.CONFIRMED:
            output_product_id = str(command.output.get("product_id") or "")
            output_product = self.runtime.product(output_product_id)
            await self.runtime.publish_event(
                FORECAST_GENERATED_EVENT,
                output_product.subject_id,
                {
                    "product_id": output_product.product_id,
                    "input_product_id": input_product.product_id,
                    **dict(output_product.data),
                },
                correlation_id=output_product.correlation_id,
                causation_id=command.command_id,
            )
            return
        await self.runtime.publish_event(
            FORECAST_FAILED_EVENT,
            WATERSHED_RESOURCE_ID,
            {
                "input_product_id": input_product.product_id,
                "command_id": command.command_id,
                "command_state": command.state.value,
                "error": command.error,
            },
            correlation_id=input_product.correlation_id,
            causation_id=command.command_id,
        )

    def _build_input_product(self, sequence: int) -> DerivedProduct | None:
        selected = self.evolution_driver.window(sequence)
        if len(selected) != FORECAST_WINDOW_POINT_COUNT:
            return None
        window_start = _timestamp(selected[0].get("observed_at"))
        window_end = _timestamp(selected[-1].get("observed_at"))
        if window_end - window_start != timedelta(hours=FORECAST_WINDOW_HOURS):
            raise ValueError("forecast input window must span exactly 24 hours")
        exceeding = [
            row for row in selected
            if _total_flow(row) > self.total_trigger_m3s
        ]
        if not exceeding:
            return None

        self._version += 1
        if self._episode_id is None:
            self._episode_id = f"flood-{window_start.strftime('%Y%m%dT%H%M%S%z')}"
        peak = max(selected, key=_total_flow)
        product_id = _forecast_input_product_id(
            self.evolution_driver.run_id,
            sequence,
        )
        boundaries: dict[str, dict[str, Any]] = {}
        for key, label in BOUNDARIES.items():
            series = [
                {
                    "time_h": round(
                        (_timestamp(row.get("observed_at")) - window_start).total_seconds()
                        / 3600,
                        3,
                    ),
                    "flow_m3s": round(float(
                        (((row.get("boundaries") or {}).get(key) or {}).get("flow_m3s"))
                        or 0
                    ), 6),
                    "source": "scenario_forecast",
                }
                for row in selected
            ]
            values = [point["flow_m3s"] for point in series]
            boundaries[key] = {
                "label": label,
                "point_count": len(series),
                "series": series,
                "peak_flow_m3s": round(max(values), 3),
                "mean_flow_m3s": round(sum(values) / len(values), 3),
                "first_flow_m3s": round(values[0], 3),
                "last_flow_m3s": round(values[-1], 3),
            }
        rainfall_series = [
            {
                "time_h": round(
                    (_timestamp(row.get("observed_at")) - window_start).total_seconds()
                    / 3600,
                    3,
                ),
                "valid_time": _timestamp(row.get("observed_at")).isoformat(),
                "rainfall_mm": round(float(row.get("rainfall_mm") or 0), 3),
            }
            for row in selected
        ]
        rainfall_total = sum(point["rainfall_mm"] for point in rainfall_series)
        current = selected[0]
        trigger = {
            "should_run_forecast": True,
            "decision": "request_forecast",
            "trigger_type": "forecast_window_peak",
            "reason": (
                f"current to +{FORECAST_WINDOW_HOURS}h boundary-flow peak "
                f"{_total_flow(peak):.3f} m3/s exceeds "
                f"{self.total_trigger_m3s:g} m3/s"
            ),
            "current_total_flow_m3s": round(_total_flow(current), 3),
            "window_peak_total_flow_m3s": round(_total_flow(peak), 3),
            "threshold_exceeded_at": str(exceeding[0]["observed_at"]),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "threshold_m3s": self.total_trigger_m3s,
            "version": self._version,
        }
        summary = {
            "boundary_flow_id": product_id,
            "episode_id": self._episode_id,
            "version": self._version,
            "mode": "domain_os_scenario_forecast",
            "generated_at": utc_now().isoformat(),
            "triggered_at": str(current["observed_at"]),
            "simulation_time": str(current["observed_at"]),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "forecast_point_count": len(selected),
            "predicted_rainfall_24h_mm": round(rainfall_total, 3),
            "rainfall_total_mm": round(rainfall_total, 3),
            "rainfall_series": rainfall_series,
            "forecast_horizon_h": FORECAST_WINDOW_HOURS,
            "reservoir_level_m": float(current.get("reservoir_level_m") or 0),
            "boundaries": boundaries,
        }
        sequence_observation_id = self.evolution_driver.sequence_observation_id(sequence)
        return DerivedProduct(
            product_id=product_id,
            product_type=FORECAST_INPUT_PRODUCT,
            subject_id=WATERSHED_RESOURCE_ID,
            producer_id=EVOLUTION_RESOURCE_ID,
            generated_at=utc_now(),
            valid_from=window_start,
            valid_to=window_end,
            input_refs=(sequence_observation_id,),
            data={
                "boundary_flow_id": product_id,
                "summary": summary,
                "forecast_trigger": trigger,
            },
            correlation_id=self._episode_id,
            causation_id=sequence_observation_id,
        )


@dataclass(frozen=True, slots=True)
class FloodForecastDomainSystem:
    runtime: DomainRuntime
    evolution_driver: BoundaryFlowEvolutionDriver
    forecast_driver: FloodForecastModelDriver
    coordinator: FloodForecastCoordinator

    async def start(self) -> None:
        await self.runtime.start()

    async def stop(self) -> None:
        await self.runtime.stop()

    async def advance(self) -> dict[str, Any] | None:
        return await self.evolution_driver.advance()

    def evolution_status(self) -> dict[str, Any]:
        driver = self.evolution_driver
        return {
            "evolution_run_id": driver.run_id,
            "source_ref": driver.source_ref,
            "sequence": driver.index - 1 if driver.index else None,
            "next_sequence": driver.index,
            "total_rows": len(driver.rows),
            "has_next": driver.has_next,
            "next_step_forecast_triggered": (
                self.coordinator.will_trigger(driver.index)
                if driver.has_next
                else False
            ),
        }

    def reset_evolution(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        run_id: str | None = None,
        source_ref: str = "boundary-flow-scenario",
    ) -> dict[str, Any]:
        self.evolution_driver.reset(
            rows,
            run_id=run_id,
            source_ref=source_ref,
        )
        self.coordinator.reset()
        return self.evolution_status()


def create_flood_forecast_domain_system(
    *,
    rows: Sequence[dict[str, Any]] | None = None,
    csv_path: Path | None = None,
    policy: DomainPolicy | None = None,
    store: DomainStore | None = None,
    runner: ForecastRunner = run_domain_cnn_forecast,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    evolution_run_id: str | None = None,
    total_trigger_m3s: float = FORECAST_TRIGGER_TOTAL_M3S,
) -> FloodForecastDomainSystem:
    selected_rows = tuple(rows) if rows is not None else tuple(load_boundary_flow_rows(csv_path))
    runtime = DomainRuntime(
        domain_id=DOMAIN_ID,
        policy=policy or RiskBasedPolicy(),
        store=store,
    )
    evolution_driver = BoundaryFlowEvolutionDriver(
        selected_rows,
        run_id=evolution_run_id,
        source_ref=str(csv_path or "domains/flood/data/mock/boundary_flow.csv"),
    )
    forecast_driver = FloodForecastModelDriver(
        runtime,
        runner=runner,
        artifact_root=artifact_root,
    )
    runtime.register_driver(evolution_driver)
    runtime.register_driver(forecast_driver)
    runtime.register_capability(Capability(
        capability_id=RUN_FLOOD_FORECAST,
        description="Run a versioned flood forecast from an immutable input product",
        risk=CapabilityRisk.LOW,
        idempotent=True,
    ))
    for boundary_key, label in BOUNDARIES.items():
        runtime.register_resource(Resource(
            resource_id=BOUNDARY_RESOURCE_IDS[boundary_key],
            resource_type="water.hydrodynamic-boundary",
            name=label,
            driver_id=evolution_driver.driver_id,
            attributes={"boundary_key": boundary_key},
        ))
    runtime.register_resource(Resource(
        resource_id=WATERSHED_RESOURCE_ID,
        resource_type="water.watershed",
        name="Shanhu watershed",
        driver_id=evolution_driver.driver_id,
    ))
    runtime.register_resource(Resource(
        resource_id=RESERVOIR_RESOURCE_ID,
        resource_type="water.reservoir",
        name="Longtan reservoir",
        driver_id=evolution_driver.driver_id,
    ))
    runtime.register_resource(Resource(
        resource_id=EVOLUTION_RESOURCE_ID,
        resource_type="water.evolution-source",
        name="Boundary-flow evolution source",
        driver_id=evolution_driver.driver_id,
        attributes={"source_kind": "scenario"},
    ))
    runtime.register_resource(Resource(
        resource_id=FORECAST_MODEL_RESOURCE_ID,
        resource_type="water.forecast-model",
        name="FLOOD CNN V2",
        driver_id=forecast_driver.driver_id,
        capabilities=frozenset({RUN_FLOOD_FORECAST}),
    ))
    coordinator = FloodForecastCoordinator(
        runtime,
        evolution_driver,
        total_trigger_m3s=total_trigger_m3s,
    )
    return FloodForecastDomainSystem(
        runtime=runtime,
        evolution_driver=evolution_driver,
        forecast_driver=forecast_driver,
        coordinator=coordinator,
    )


def _forecast_input_product_id(run_id: str, sequence: int) -> str:
    return f"water.flood.forecast-input/{run_id}/{sequence:06d}"


def _forecast_product_id(input_product_id: str) -> str:
    prefix = "water.flood.forecast-input/"
    if not input_product_id.startswith(prefix):
        raise ValueError(f"invalid forecast input product id: {input_product_id}")
    return f"water.flood.forecast/{input_product_id.removeprefix(prefix)}"


def _forecast_artifacts(depth_path: Path) -> dict[str, str]:
    candidates = {
        "max_depth": depth_path,
        "depth_series": depth_path.with_name("depth_series.npy"),
        "time_steps": depth_path.with_name("time_steps.json"),
    }
    return {
        name: _artifact_reference(path)
        for name, path in candidates.items()
        if path.exists()
    }


def _artifact_reference(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_DIR))
    except ValueError:
        return str(resolved)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("evolution timestamp must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evolution timestamp must include a timezone")
    return parsed


def _total_flow(row: dict[str, Any]) -> float:
    boundaries = row.get("boundaries") or {}
    if boundaries:
        return sum(
            float((boundaries.get(key) or {}).get("flow_m3s") or 0)
            for key in BOUNDARIES
        )
    return float(row.get("total_flow_m3s") or 0)
