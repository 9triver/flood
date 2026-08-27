from __future__ import annotations

from collections.abc import Iterable, Sequence

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)

from .paths import (
    ASSESSMENTS_BASE,
    PRODUCTS_BASE,
    latest_product_path,
    product_path,
)


PUBLISH_ASSESSMENT = "publish_assessment"


class FloodProductDriver(Driver):
    """Publish immutable domain products and mutable latest pointers."""

    device_id = "flood:products"
    operation_timeout_seconds = 30.0

    def publish(
        self,
        kind: str,
        product_id: str,
        *,
        operation_id: str,
        data: dict,
        status: str = "ready",
        error: str | None = None,
        observed_at: float | None = None,
    ) -> str:
        reference = product_path(kind, product_id)
        self.kernel.interrupt(
            self.device_id,
            {
                "kind": "publish",
                "product_kind": kind,
                "product_id": product_id,
                "operation_id": operation_id,
                "status": status,
                "error": error,
                "data": dict(data),
                "observed_at": (
                    float(observed_at)
                    if observed_at is not None
                    else self.kernel.clock()
                ),
            },
        )
        return reference

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict) or raw.get("kind") != "publish":
            raise ValueError("product driver accepts only publish frames")
        kind = str(raw["product_kind"])
        product_id = str(raw["product_id"])
        reference = product_path(kind, product_id)
        observed_at = float(raw["observed_at"])
        product = {
            "kind": "product",
            "product_kind": kind,
            "product_id": product_id,
            "operation_id": str(raw["operation_id"]),
            "status": str(raw.get("status") or "ready"),
            "error": raw.get("error"),
            "data": dict(raw.get("data") or {}),
            "generated_at": observed_at,
        }
        yield NormalizedObservation(
            reference,
            product,
            observed_at,
            self.device_id,
        )
        yield NormalizedObservation(
            latest_product_path(kind),
            {
                "product_kind": kind,
                "product_id": product_id,
                "ref": reference,
                "status": product["status"],
            },
            observed_at,
            self.device_id,
        )

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        if path != ASSESSMENTS_BASE or action != PUBLISH_ASSESSMENT:
            return f"unsupported product action: {action} on {path}"
        basis_refs = arguments.get("basis_refs")
        conclusions = arguments.get("conclusions")
        if not isinstance(basis_refs, list):
            return "arguments.basis_refs must be a list"
        if not isinstance(conclusions, list) or not conclusions:
            return "arguments.conclusions must be a non-empty list"
        return None

    def dispatch(self, operation: Operation) -> None:
        product_id = f"assessment_{operation.operation_id.removeprefix('op_')}"
        self.publish(
            "assessment",
            product_id,
            operation_id=operation.operation_id,
            data={
                **operation.arguments,
                "source": "agent",
            },
        )

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return verification_from_product(operation, evidence, "assessment")


def verification_from_product(
    operation: Operation,
    evidence: Sequence[Observation],
    product_kind: str,
) -> Verification:
    prefix = product_path(product_kind, "")
    for observation in reversed(evidence):
        if not observation.path.startswith(prefix):
            continue
        value = observation.value
        if not isinstance(value, dict):
            continue
        if value.get("operation_id") != operation.operation_id:
            continue
        status = value.get("status")
        if status == "ready":
            return Verification.committed()
        if status == "failed":
            return Verification.failed(str(value.get("error") or "product failed"))
    return Verification.pending()
