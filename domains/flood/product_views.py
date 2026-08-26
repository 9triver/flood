"""Compatibility views that read GIS data directly from Domain OS products."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

from domain_os import DerivedProduct, DomainQueryService, DomainRecordNotFound

from .forecast_domain import FORECAST_INPUT_PRODUCT, FORECAST_PRODUCT, PROJECT_DIR
from .impact_domain import IMPACT_PRODUCT
from .runtime.forecast import read_hydrodynamic_depth_csv
from .runtime.hydrodynamic_grid import HydrodynamicMeshStore, STORE
from .runtime.impact_analysis import BRIDGE_INFLUENCE_RADIUS_M


class FloodProductViews:
    """Translate immutable products to the DTOs used by the existing GIS."""

    def __init__(
        self,
        queries: DomainQueryService,
        *,
        mesh: HydrodynamicMeshStore = STORE,
    ) -> None:
        self.queries = queries
        self.mesh = mesh

    def forecast_grid_meta(self, product_id: str) -> dict[str, Any]:
        product = self._forecast(product_id)
        depths, _, _ = _forecast_depths(product, None)
        return self.mesh.meta_from_depths(
            self._forecast_metadata(product, depths),
            depths,
        )

    def forecast_grid_tile(
        self,
        z: int,
        x: int,
        y: int,
        product_id: str,
        *,
        wet_only: bool = False,
        time_h: float | None = None,
        tile_crs: str = "wgs84",
    ) -> dict[str, Any]:
        product = self._forecast(product_id)
        depths, actual_time_h, time_index = _forecast_depths(product, time_h)
        return self.mesh.tile_from_depths(
            z,
            x,
            y,
            depths,
            source_id=product.product_id,
            result_version=_result_version(product),
            wet_only=wet_only,
            time_h=actual_time_h,
            time_index=time_index,
            tile_crs=tile_crs,
        )

    def impact_assessment(self, product_id: str) -> dict[str, Any]:
        product = self.queries.product_record(product_id)
        if product.product_type != IMPACT_PRODUCT:
            raise ValueError(
                f"product is not an impact assessment: {product.product_id}"
            )
        return _impact_dto(product)

    def impact_for_forecast(
        self,
        forecast_product_id: str,
        *,
        target_type: str = "all",
        min_depth_m: float = 0.15,
        max_distance_m: float = 10.0,
        bridge_influence_radius_m: float = BRIDGE_INFLUENCE_RADIUS_M,
        time_h: float | None = None,
    ) -> dict[str, Any]:
        selected_forecast = str(forecast_product_id or "").strip()
        if not selected_forecast:
            raise ValueError("forecast_product_id is required")
        expected = {
            "target_type": str(target_type or "all"),
            "min_depth_m": float(min_depth_m),
            "max_distance_m": float(max_distance_m),
            "bridge_influence_radius_m": float(bridge_influence_radius_m),
            "time_h": float(time_h) if time_h is not None else None,
        }
        products = self.queries.source.products(product_type=IMPACT_PRODUCT)
        for product in reversed(products):
            if product.input_refs != (selected_forecast,):
                continue
            parameters = dict(product.data.get("parameters") or {})
            if _parameters_match(parameters, expected):
                return _impact_dto(product)
        raise DomainRecordNotFound(
            "no impact assessment matches forecast product and parameters: "
            f"{selected_forecast}"
        )

    def _forecast(self, product_id: str) -> DerivedProduct:
        product = self.queries.product_record(product_id)
        if product.product_type != FORECAST_PRODUCT:
            raise ValueError(f"product is not a flood forecast: {product.product_id}")
        return product

    def _forecast_metadata(
        self,
        product: DerivedProduct,
        depths: dict[int, float],
    ) -> dict[str, Any]:
        steps = _time_steps(product)
        return {
            "forecast_id": product.product_id,
            "forecast_version": product.product_id,
            "forecast_time": (
                product.valid_from.isoformat()
                if product.valid_from is not None
                else product.generated_at.isoformat()
            ),
            "valid_from": product.valid_from.isoformat() if product.valid_from else None,
            "valid_to": product.valid_to.isoformat() if product.valid_to else None,
            "generated_at": product.generated_at.isoformat(),
            "lead_time_h": _horizon_hours(product),
            "rainfall_series": self._rainfall_series(product),
            "result_version": _result_version(product),
            "depth_path": str(product.artifacts.get("max_depth") or ""),
            "series_path": str(product.artifacts.get("depth_series") or ""),
            "depth_count": len(depths),
            "flooded_count": sum(depth > 0 for depth in depths.values()),
            "max_depth_m": round(max(depths.values(), default=0.0), 4),
            "time_steps_h": steps,
            "time_steps": [
                {
                    "time_h": time_h,
                    "valid_at": (
                        product.valid_from + timedelta(hours=time_h)
                    ).isoformat() if product.valid_from is not None else None,
                }
                for time_h in steps
            ],
            "time_step_count": len(steps),
            "product_type": product.product_type,
            "producer_id": product.producer_id,
        }

    def _rainfall_series(self, product: DerivedProduct) -> list[dict[str, Any]]:
        for reference in product.input_refs:
            try:
                input_product = self.queries.product_record(reference)
            except DomainRecordNotFound:
                continue
            if input_product.product_type != FORECAST_INPUT_PRODUCT:
                continue
            summary = input_product.data.get("summary") or {}
            series = summary.get("rainfall_series") or []
            return [dict(item) for item in series if isinstance(item, dict)]
        return []


def _forecast_depths(
    product: DerivedProduct,
    requested_time_h: float | None,
) -> tuple[dict[int, float], float | None, int | None]:
    if requested_time_h is None:
        path = _artifact_path(product, "max_depth")
        return read_hydrodynamic_depth_csv(path), None, None

    steps = _time_steps(product)
    if not steps:
        raise ValueError("forecast product has no time steps")
    requested = float(requested_time_h)
    if requested < 0:
        raise ValueError("time_h must not be negative")
    index = min(range(len(steps)), key=lambda item: abs(steps[item] - requested))
    series_path = _artifact_path(product, "depth_series")
    array = np.load(series_path, mmap_mode="r")
    if array.ndim != 2 or index >= int(array.shape[0]):
        raise ValueError("forecast depth series shape does not match time steps")
    values = np.asarray(array[index], dtype=np.float32)
    wet_indices = np.flatnonzero(values > 0)
    return (
        {int(item) + 1: float(values[item]) for item in wet_indices},
        steps[index],
        index,
    )


def _time_steps(product: DerivedProduct) -> list[float]:
    raw = product.data.get("time_steps_h")
    if isinstance(raw, (list, tuple)) and raw:
        return [float(value) for value in raw]
    reference = str(product.artifacts.get("time_steps") or "").strip()
    if not reference:
        return []
    path = _artifact_path(product, "time_steps")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"forecast time steps are invalid: {path}") from exc
    return [float(value) for value in payload.get("time_steps_h") or []]


def _artifact_path(product: DerivedProduct, name: str) -> Path:
    reference = str(product.artifacts.get(name) or "").strip()
    if not reference:
        raise ValueError(f"forecast product has no {name} artifact")
    unresolved = Path(reference).expanduser()
    path = unresolved.resolve() if unresolved.is_absolute() else (PROJECT_DIR / unresolved).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"forecast product artifact is missing: {path}")
    return path


def _result_version(product: DerivedProduct) -> str:
    parts = [product.product_id]
    for name in ("max_depth", "depth_series", "time_steps"):
        reference = str(product.artifacts.get(name) or "").strip()
        if not reference:
            parts.append(f"{name}:missing")
            continue
        unresolved = Path(reference).expanduser()
        path = unresolved.resolve() if unresolved.is_absolute() else (PROJECT_DIR / unresolved).resolve()
        try:
            stat = path.stat()
        except OSError:
            parts.append(f"{name}:missing")
        else:
            parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts)


def _horizon_hours(product: DerivedProduct) -> float | None:
    if product.valid_from is None or product.valid_to is None:
        return None
    return round((product.valid_to - product.valid_from).total_seconds() / 3600, 6)


def _impact_dto(product: DerivedProduct) -> dict[str, Any]:
    result = dict(product.data)
    result["assessment_product_id"] = product.product_id
    result["forecast_product_id"] = (
        product.input_refs[0] if product.input_refs else result.get("forecast_product_id")
    )
    result.setdefault("forecast_id", result["forecast_product_id"])
    result["generated_at"] = product.generated_at.isoformat()
    result["valid_from"] = product.valid_from.isoformat() if product.valid_from else None
    result["valid_to"] = product.valid_to.isoformat() if product.valid_to else None
    result["input_refs"] = list(product.input_refs)
    return result


def _parameters_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if str(actual.get("target_type") or "all") != expected["target_type"]:
        return False
    for key in (
        "min_depth_m",
        "max_distance_m",
        "bridge_influence_radius_m",
    ):
        try:
            if abs(float(actual.get(key)) - float(expected[key])) > 1e-9:
                return False
        except (TypeError, ValueError):
            return False
    actual_time = actual.get("time_h")
    expected_time = expected["time_h"]
    if actual_time is None or expected_time is None:
        return actual_time is None and expected_time is None
    try:
        return abs(float(actual_time) - float(expected_time)) <= 1e-9
    except (TypeError, ValueError):
        return False
