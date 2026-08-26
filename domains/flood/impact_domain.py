"""Inundation impact assessment built on versioned Domain OS products."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np

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
    ObservationSink,
    Resource,
    new_id,
    utc_now,
)

from .forecast_domain import (
    DEFAULT_ARTIFACT_ROOT,
    FORECAST_GENERATED_EVENT,
    FORECAST_PRODUCT,
    WATERSHED_RESOURCE_ID,
    FloodForecastDomainSystem,
    ForecastRunner,
    create_flood_forecast_domain_system,
    run_domain_cnn_forecast,
)
from .runtime.boundary_flow import FORECAST_TRIGGER_TOTAL_M3S
from .runtime.forecast import (
    forecast_cells_from_hydrodynamic_mesh,
    read_hydrodynamic_depth_csv,
)
from .runtime.hydrodynamic_grid import ensure_hydrodynamic_mesh
from .runtime.impact_analysis import (
    BRIDGE_INFLUENCE_RADIUS_M,
    analyze_inundation_cells,
    resolve_target_types,
)
from .runtime.repository import FloodRepository


IMPACT_DRIVER_ID = "water.infrastructure.inundation-impact-analysis"
IMPACT_MODEL_RESOURCE_ID = "water.model/inundation-impact-analyzer"
RUN_IMPACT_ANALYSIS = "water.flood.analyze-impacts"
IMPACT_PRODUCT = "water.flood.impact-assessment"
IMPACT_REQUIRED_EVENT = "water.flood.impact-analysis.required"
IMPACT_GENERATED_EVENT = "water.flood.impact-assessment.generated"
IMPACT_FAILED_EVENT = "water.flood.impact-analysis.failed"

PROJECT_DIR = Path(__file__).resolve().parents[2]
OBJECT_MANIFEST_PATH = PROJECT_DIR / "domains" / "flood" / "data" / "objects" / "manifest.json"

ImpactRunner = Callable[[DerivedProduct, Mapping[str, Any]], dict[str, Any]]


def run_domain_impact_analysis(
    resolver: Any,
    forecast_product: DerivedProduct,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    time_h = parameters.get("time_h")
    depths, actual_time_h = _product_depths(forecast_product, time_h)
    ensure_hydrodynamic_mesh()
    cells = forecast_cells_from_hydrodynamic_mesh(
        depths,
        generated_at=forecast_product.generated_at.isoformat(),
        time_h=actual_time_h,
        forecast_id=forecast_product.product_id,
    )
    valid_at = (
        forecast_product.valid_from + timedelta(hours=actual_time_h)
        if forecast_product.valid_from is not None and actual_time_h is not None
        else None
    )
    result = analyze_inundation_cells(
        resolver,
        cells,
        forecast_id=forecast_product.product_id,
        target_type=str(parameters["target_type"]),
        min_depth_m=float(parameters["min_depth_m"]),
        max_distance_m=float(parameters["max_distance_m"]),
        time_h=actual_time_h,
        bridge_influence_radius_m=float(parameters["bridge_influence_radius_m"]),
        time_context={
            "forecast_time": forecast_product.generated_at.isoformat(),
            "valid_from": (
                forecast_product.valid_from.isoformat()
                if forecast_product.valid_from is not None
                else None
            ),
            "valid_to": (
                forecast_product.valid_to.isoformat()
                if forecast_product.valid_to is not None
                else None
            ),
            "analysis_time_at": valid_at.isoformat() if valid_at is not None else None,
        },
    )
    result["object_library_version"] = _object_library_version()
    result["forecast_cell_count_analyzed"] = len(cells)
    result.setdefault("affected_object_ids", {})
    return result


class FloodImpactAnalysisDriver(InfrastructureDriver):
    """Turns an immutable forecast product into an impact assessment product."""

    driver_id = IMPACT_DRIVER_ID

    def __init__(
        self,
        runtime: DomainRuntime,
        *,
        runner: ImpactRunner,
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
            return CommandResult(
                accepted=False,
                error="impact analysis driver is not started",
            )
        if command.intent.capability_id != RUN_IMPACT_ANALYSIS:
            return CommandResult(
                accepted=False,
                error=f"unsupported capability: {command.intent.capability_id}",
            )
        forecast_product_id = str(
            command.intent.arguments.get("forecast_product_id") or ""
        ).strip()
        if not forecast_product_id:
            return CommandResult(
                accepted=False,
                error="forecast_product_id is required",
            )
        try:
            forecast_product = self.runtime.product(forecast_product_id)
        except DomainRuntimeError as exc:
            return CommandResult(accepted=False, error=str(exc))
        if forecast_product.product_type != FORECAST_PRODUCT:
            return CommandResult(
                accepted=False,
                error=f"unsupported impact input product: {forecast_product.product_type}",
            )
        try:
            parameters = _impact_parameters(command.intent.arguments)
            _validate_time_h(forecast_product, parameters.get("time_h"))
        except (TypeError, ValueError) as exc:
            return CommandResult(accepted=False, error=str(exc))

        product_id = _impact_product_id(forecast_product.product_id, parameters)
        try:
            existing_product = self.runtime.product(product_id)
        except DomainRuntimeError:
            existing_product = None
        if existing_product is not None:
            if (
                existing_product.product_type != IMPACT_PRODUCT
                or existing_product.input_refs != (forecast_product.product_id,)
            ):
                return CommandResult(
                    accepted=False,
                    error=f"impact product id is already in use: {product_id}",
                )
            return CommandResult(
                accepted=True,
                external_id=f"impact-{command.command_id}",
                output={
                    "product_id": existing_product.product_id,
                    "total_impacts": int(existing_product.data.get("total_impacts") or 0),
                    "reused": True,
                },
                products=(existing_product,),
            )

        try:
            result = await asyncio.to_thread(
                self.runner,
                forecast_product,
                parameters,
            )
        except Exception as exc:
            return CommandResult(
                accepted=False,
                error=f"impact analysis execution failed: {exc}",
            )
        if not isinstance(result, dict):
            return CommandResult(
                accepted=False,
                error="impact analysis returned an invalid result",
            )
        if result.get("error"):
            return CommandResult(accepted=False, error=str(result["error"]))
        if result.get("status") not in {"completed", "no_forecast_cells"}:
            return CommandResult(
                accepted=False,
                error=f"impact analysis returned status: {result.get('status')}",
            )

        output_dir = self.artifact_root / _safe_component(product_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "impact_report.json"
        product_data = {
            **result,
            "forecast_product_id": forecast_product.product_id,
            "analysis_signature": _impact_signature(parameters),
            "parameters": parameters,
        }
        valid_from, valid_to = _assessment_validity(forecast_product, result)
        report_path.write_text(
            json.dumps(product_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        product = DerivedProduct(
            product_id=product_id,
            product_type=IMPACT_PRODUCT,
            subject_id=WATERSHED_RESOURCE_ID,
            producer_id=IMPACT_MODEL_RESOURCE_ID,
            generated_at=utc_now(),
            valid_from=valid_from,
            valid_to=valid_to,
            input_refs=(forecast_product.product_id,),
            data=product_data,
            artifacts={"impact_report": _artifact_reference(report_path)},
            correlation_id=forecast_product.correlation_id,
            causation_id=command.command_id,
        )
        return CommandResult(
            accepted=True,
            external_id=f"impact-{command.command_id}",
            output={
                "product_id": product.product_id,
                "total_impacts": int(product.data.get("total_impacts") or 0),
            },
            products=(product,),
        )


class FloodImpactCoordinator:
    """Submits default impact analysis after a forecast product is generated."""

    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime
        self._dispose = runtime.subscribe(
            self._on_forecast_generated,
            event_type=FORECAST_GENERATED_EVENT,
        )

    def close(self) -> None:
        self._dispose()

    async def _on_forecast_generated(self, event) -> None:
        forecast_product_id = str(event.data.get("product_id") or "").strip()
        if not forecast_product_id:
            return
        parameters = _impact_parameters({})
        await self.runtime.publish_event(
            IMPACT_REQUIRED_EVENT,
            WATERSHED_RESOURCE_ID,
            {
                "forecast_product_id": forecast_product_id,
                "parameters": parameters,
            },
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
        )
        command = await self.runtime.submit_intent(Intent(
            intent_id=new_id("intent"),
            actor_id="water.rule.forecast-impact-analysis",
            resource_id=IMPACT_MODEL_RESOURCE_ID,
            capability_id=RUN_IMPACT_ANALYSIS,
            arguments={
                "forecast_product_id": forecast_product_id,
                **parameters,
            },
            requested_at=utc_now(),
            rationale="Assess object impacts for the generated flood forecast",
            correlation_id=event.correlation_id,
        ))
        if command.state is CommandState.CONFIRMED:
            product_id = str(command.output.get("product_id") or "")
            product = self.runtime.product(product_id)
            await self.runtime.publish_event(
                IMPACT_GENERATED_EVENT,
                product.subject_id,
                {
                    "product_id": product.product_id,
                    "forecast_product_id": forecast_product_id,
                    "status": product.data.get("status"),
                    "summary": dict(product.data.get("summary") or {}),
                    "affected_object_ids": dict(
                        product.data.get("affected_object_ids") or {}
                    ),
                    "total_impacts": int(product.data.get("total_impacts") or 0),
                },
                correlation_id=product.correlation_id,
                causation_id=command.command_id,
            )
            return
        await self.runtime.publish_event(
            IMPACT_FAILED_EVENT,
            WATERSHED_RESOURCE_ID,
            {
                "forecast_product_id": forecast_product_id,
                "command_id": command.command_id,
                "command_state": command.state.value,
                "error": command.error,
            },
            correlation_id=event.correlation_id,
            causation_id=command.command_id,
        )


@dataclass(frozen=True, slots=True)
class FloodImpactDomainSystem:
    forecast_system: FloodForecastDomainSystem
    impact_driver: FloodImpactAnalysisDriver
    impact_coordinator: FloodImpactCoordinator

    @property
    def runtime(self) -> DomainRuntime:
        return self.forecast_system.runtime

    async def start(self) -> None:
        await self.forecast_system.start()

    async def stop(self) -> None:
        await self.forecast_system.stop()

    async def advance(self) -> dict[str, Any] | None:
        return await self.forecast_system.advance()

    def evolution_status(self) -> dict[str, Any]:
        return self.forecast_system.evolution_status()

    def reset_evolution(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        run_id: str | None = None,
        source_ref: str = "boundary-flow-scenario",
    ) -> dict[str, Any]:
        return self.forecast_system.reset_evolution(
            rows,
            run_id=run_id,
            source_ref=source_ref,
        )


def create_flood_impact_domain_system(
    *,
    rows: Sequence[dict[str, Any]] | None = None,
    csv_path: Path | None = None,
    policy: DomainPolicy | None = None,
    store: DomainStore | None = None,
    forecast_runner: ForecastRunner = run_domain_cnn_forecast,
    impact_runner: ImpactRunner | None = None,
    resolver: Any | None = None,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    evolution_run_id: str | None = None,
    total_trigger_m3s: float = FORECAST_TRIGGER_TOTAL_M3S,
) -> FloodImpactDomainSystem:
    forecast_system = create_flood_forecast_domain_system(
        rows=rows,
        csv_path=csv_path,
        policy=policy,
        store=store,
        runner=forecast_runner,
        artifact_root=artifact_root,
        evolution_run_id=evolution_run_id,
        total_trigger_m3s=total_trigger_m3s,
    )
    selected_resolver = resolver or FloodRepository()
    selected_runner = impact_runner or partial(
        run_domain_impact_analysis,
        selected_resolver,
    )
    impact_driver = FloodImpactAnalysisDriver(
        forecast_system.runtime,
        runner=selected_runner,
        artifact_root=artifact_root,
    )
    forecast_system.runtime.register_driver(impact_driver)
    forecast_system.runtime.register_capability(Capability(
        capability_id=RUN_IMPACT_ANALYSIS,
        description="Create a versioned object impact assessment from a flood forecast",
        risk=CapabilityRisk.LOW,
        idempotent=True,
    ))
    forecast_system.runtime.register_resource(Resource(
        resource_id=IMPACT_MODEL_RESOURCE_ID,
        resource_type="water.impact-analysis-model",
        name="Deterministic inundation impact analyzer",
        driver_id=impact_driver.driver_id,
        capabilities=frozenset({RUN_IMPACT_ANALYSIS}),
    ))
    impact_coordinator = FloodImpactCoordinator(forecast_system.runtime)
    return FloodImpactDomainSystem(
        forecast_system=forecast_system,
        impact_driver=impact_driver,
        impact_coordinator=impact_coordinator,
    )


def _impact_parameters(arguments: Mapping[str, Any]) -> dict[str, Any]:
    requested_target = str(arguments.get("target_type") or "all").strip() or "all"
    target_types = resolve_target_types(requested_target)
    if not target_types:
        raise ValueError(f"invalid target_type: {requested_target}")
    target_type = "all" if requested_target.lower() == "all" else target_types[0]
    return {
        "target_type": target_type,
        "min_depth_m": _nonnegative_float(
            arguments.get("min_depth_m", 0.15),
            "min_depth_m",
        ),
        "max_distance_m": _nonnegative_float(
            arguments.get("max_distance_m", 10.0),
            "max_distance_m",
        ),
        "bridge_influence_radius_m": _nonnegative_float(
            arguments.get(
                "bridge_influence_radius_m",
                BRIDGE_INFLUENCE_RADIUS_M,
            ),
            "bridge_influence_radius_m",
        ),
        "time_h": _optional_nonnegative_float(arguments.get("time_h"), "time_h"),
    }


def _impact_product_id(
    forecast_product_id: str,
    parameters: Mapping[str, Any],
) -> str:
    prefix = "water.flood.forecast/"
    if not forecast_product_id.startswith(prefix):
        raise ValueError(f"invalid forecast product id: {forecast_product_id}")
    return (
        "water.flood.impact-assessment/"
        f"{forecast_product_id.removeprefix(prefix)}/"
        f"{_impact_signature(parameters)}"
    )


def _impact_signature(parameters: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(parameters),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _product_depths(
    product: DerivedProduct,
    requested_time_h: Any,
) -> tuple[dict[int, float], float | None]:
    if requested_time_h is None:
        path = _artifact_path(product, "max_depth")
        if not path.exists():
            raise FileNotFoundError(f"forecast max-depth artifact is missing: {path}")
        return read_hydrodynamic_depth_csv(path), None

    series_path = _artifact_path(product, "depth_series")
    steps_path = _artifact_path(product, "time_steps")
    if not series_path.exists() or not steps_path.exists():
        raise FileNotFoundError("forecast time-series artifacts are missing")
    try:
        payload = json.loads(steps_path.read_text(encoding="utf-8"))
        steps = [float(value) for value in payload.get("time_steps_h") or []]
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("forecast time steps are invalid") from exc
    if not steps:
        raise ValueError("forecast time steps are empty")
    requested = float(requested_time_h)
    index = min(range(len(steps)), key=lambda item: abs(steps[item] - requested))
    array = np.load(series_path, mmap_mode="r")
    if array.ndim != 2 or index >= int(array.shape[0]):
        raise ValueError("forecast depth series shape does not match time steps")
    values = np.asarray(array[index], dtype=np.float32)
    wet_indices = np.flatnonzero(values > 0)
    return (
        {int(item) + 1: float(values[item]) for item in wet_indices},
        steps[index],
    )


def _artifact_path(product: DerivedProduct, name: str) -> Path:
    reference = str(product.artifacts.get(name) or "").strip()
    if not reference:
        raise ValueError(f"forecast product has no {name} artifact")
    path = Path(reference).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_DIR / path).resolve()


def _artifact_reference(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_DIR))
    except ValueError:
        return str(resolved)


def _object_library_version() -> str:
    try:
        content = OBJECT_MANIFEST_PATH.read_bytes()
    except OSError:
        return "unavailable"
    return hashlib.sha256(content).hexdigest()[:16]


def _validate_time_h(product: DerivedProduct, time_h: Any) -> None:
    if time_h is None or product.valid_from is None or product.valid_to is None:
        return
    horizon_h = (product.valid_to - product.valid_from).total_seconds() / 3600
    if float(time_h) > horizon_h:
        raise ValueError(f"time_h exceeds forecast horizon: {horizon_h:g}")


def _assessment_validity(
    forecast_product: DerivedProduct,
    result: Mapping[str, Any],
) -> tuple[datetime | None, datetime | None]:
    actual_time_h = result.get("time_h")
    if actual_time_h is None or forecast_product.valid_from is None:
        return forecast_product.valid_from, forecast_product.valid_to
    valid_at = forecast_product.valid_from + timedelta(hours=float(actual_time_h))
    return valid_at, valid_at


def _nonnegative_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return result


def _optional_nonnegative_float(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    return _nonnegative_float(value, label)


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
