from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Sequence
from typing import Callable

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)

from .paths import ROUTING_PATH
from .products import FloodProductDriver, verification_from_product


PLAN_ROUTE = "plan_route"
RouteRunner = Callable[[tuple[float, float], tuple[float, float], str], dict]


class RoutingServiceDriver(Driver):
    device_id = "flood:service:routing:amap"
    operation_timeout_seconds = 60.0

    def __init__(
        self,
        products: FloodProductDriver,
        runner: RouteRunner,
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
            raise ValueError("routing driver accepts only descriptor frames")
        yield NormalizedObservation(
            ROUTING_PATH,
            {
                "kind": "service",
                "domain": "flood",
                "service_type": "route_planning",
                "provider": "amap",
                "implementation": self.implementation,
                "actions": [PLAN_ROUTE],
                "profiles": ["car", "foot"],
                "input_crs": "EPSG:4326",
                "output_product": "route",
            },
            float(raw["observed_at"]),
            self.device_id,
        )

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        if path != ROUTING_PATH or action != PLAN_ROUTE:
            return f"unsupported routing action: {action} on {path}"
        if arguments.get("profile", "car") not in {"car", "foot"}:
            return "arguments.profile must be car or foot"
        for name in ("start_ref", "destination_ref"):
            reference = arguments.get(name)
            if not isinstance(reference, str) or not reference:
                return f"arguments.{name} is required"
            snapshot = self.kernel.read(reference)
            if snapshot is None:
                return f"unknown resource: {reference}"
            if _resource_point(snapshot.value) is None:
                return f"resource has no usable geometry: {reference}"
        return None

    def dispatch(self, operation: Operation) -> None:
        product_id = f"route_{operation.operation_id.removeprefix('op_')}"
        threading.Thread(
            target=self._run,
            args=(operation, product_id),
            daemon=True,
            name=f"domain-os-mvp-{product_id}",
        ).start()

    def _run(self, operation: Operation, product_id: str) -> None:
        start_ref = operation.arguments["start_ref"]
        destination_ref = operation.arguments["destination_ref"]
        try:
            start = _resource_point(self.kernel.read(start_ref).value)
            destination = _resource_point(self.kernel.read(destination_ref).value)
            if start is None or destination is None:
                raise ValueError("route endpoints no longer have usable geometry")
            route = self.runner(
                start,
                destination,
                str(operation.arguments.get("profile") or "car"),
            )
        except Exception as exc:
            self.products.publish(
                "route",
                product_id,
                operation_id=operation.operation_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                data={
                    "service_ref": ROUTING_PATH,
                    "start_ref": start_ref,
                    "destination_ref": destination_ref,
                },
            )
            return
        self.products.publish(
            "route",
            product_id,
            operation_id=operation.operation_id,
            data={
                "service_ref": ROUTING_PATH,
                "start_ref": start_ref,
                "destination_ref": destination_ref,
                "profile": operation.arguments.get("profile") or "car",
                "forecast_ref": operation.arguments.get("forecast_ref"),
                "route": route,
            },
        )

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return verification_from_product(operation, evidence, "route")


class DirectRouteRunner:
    """Offline route surrogate preserving the Amap service contract."""

    def __call__(
        self,
        start: tuple[float, float],
        destination: tuple[float, float],
        profile: str,
    ) -> dict:
        distance = _distance_m(start, destination)
        speed_mps = 1.2 if profile == "foot" else 8.0
        return {
            "provider": "offline-direct-surrogate",
            "is_surrogate": True,
            "profile": profile,
            "distance_m": round(distance, 1),
            "duration_seconds": round(distance / speed_mps, 1),
            "geometry": {
                "type": "LineString",
                "coordinates": [list(start), list(destination)],
            },
        }


class AmapRouteRunner:
    """Real Amap Web Service adapter using the existing coordinate logic."""

    def __init__(self, api_key: str, *, timeout_seconds: float = 20.0):
        if not api_key:
            raise ValueError("Amap API key is required")
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)

    def __call__(
        self,
        start: tuple[float, float],
        destination: tuple[float, float],
        profile: str,
    ) -> dict:
        from domains.flood.runtime.route_planning import (
            amap_request,
            amap_route_paths,
            call_amap,
        )

        payload = amap_request(start, destination, profile)
        response = call_amap(self.api_key, payload, self.timeout_seconds)
        candidates = amap_route_paths(response, start, destination, profile)
        selected = min(candidates, key=lambda item: float(item["distance"]))
        return {
            "provider": "amap",
            "is_surrogate": False,
            "profile": profile,
            "distance_m": selected["distance"],
            "duration_seconds": float(selected["time"]) / 1000.0,
            "geometry": selected["points"],
            "instructions": selected["instructions"],
            "candidate_count": len(candidates),
        }


def _resource_point(resource: object) -> tuple[float, float] | None:
    if not isinstance(resource, dict):
        return None
    geometry = resource.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = list(_coordinate_pairs(geometry.get("coordinates")))
    if not coordinates:
        return None
    return (
        sum(item[0] for item in coordinates) / len(coordinates),
        sum(item[1] for item in coordinates) / len(coordinates),
    )


def _coordinate_pairs(value):
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _coordinate_pairs(item)


def _distance_m(
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    lon1, lat1 = map(math.radians, start)
    lon2, lat2 = map(math.radians, end)
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
