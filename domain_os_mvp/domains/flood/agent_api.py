from __future__ import annotations

from .hydrodynamic import RUN_FORECAST
from .paths import (
    ASSESSMENTS_BASE,
    MODEL_PATH,
    ROUTING_PATH,
    asset_index_path,
    latest_product_path,
)
from .products import PUBLISH_ASSESSMENT
from .routing import PLAN_ROUTE


class FloodDomainClient:
    """Agent-facing domain SDK compiled entirely to the six syscalls."""

    def __init__(self, kernel, capabilities: dict[str, str]):
        self.kernel = kernel
        self.capabilities = dict(capabilities)

    def read(self, path: str):
        return self.kernel.read(path)

    def history(self, path: str, *, since: float | None = None):
        return self.kernel.history(path, since=since)

    def watch(
        self,
        path: str,
        *,
        after_revision: int = 0,
        timeout: float | None = None,
    ):
        return self.kernel.watch(
            path,
            after_revision=after_revision,
            timeout=timeout,
        )

    def operation(self, operation_id: str):
        return self.kernel.operation(operation_id)

    def list_asset_refs(self, object_type: str) -> list[str]:
        snapshot = self.read(asset_index_path(object_type))
        return list(snapshot.value["refs"]) if snapshot is not None else []

    def latest_product(self, kind: str):
        pointer = self.read(latest_product_path(kind))
        if pointer is None:
            return None
        return self.read(pointer.value["ref"])

    def run_forecast(self, arguments: dict):
        model = self.read(MODEL_PATH)
        return self.kernel.act(
            self.capabilities["forecast"],
            MODEL_PATH,
            RUN_FORECAST,
            arguments,
            expected_revision=model.revision if model else 0,
        )

    def plan_route(
        self,
        start_ref: str,
        destination_ref: str,
        *,
        profile: str = "car",
        forecast_ref: str | None = None,
    ):
        service = self.read(ROUTING_PATH)
        return self.kernel.act(
            self.capabilities["routing"],
            ROUTING_PATH,
            PLAN_ROUTE,
            {
                "start_ref": start_ref,
                "destination_ref": destination_ref,
                "profile": profile,
                "forecast_ref": forecast_ref,
            },
            expected_revision=service.revision if service else 0,
        )

    def publish_assessment(
        self,
        *,
        basis_refs: list[str],
        conclusions: list[dict],
        recommendations: list[dict] | None = None,
        agent: dict | None = None,
    ):
        return self.kernel.act(
            self.capabilities["assessment"],
            ASSESSMENTS_BASE,
            PUBLISH_ASSESSMENT,
            {
                "basis_refs": list(basis_refs),
                "conclusions": list(conclusions),
                "recommendations": list(recommendations or []),
                "agent": dict(agent or {}),
            },
        )
