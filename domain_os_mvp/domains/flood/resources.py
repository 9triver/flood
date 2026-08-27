from __future__ import annotations

from collections.abc import Iterable, Sequence

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)


class DescriptorDriver(Driver):
    """Expose one read-only domain resource descriptor in world state."""

    def __init__(self, device_id: str, path: str, descriptor: dict):
        self.device_id = device_id
        self.path = path
        self.descriptor = dict(descriptor)
        self.operation_timeout_seconds = None

    def bootstrap(self) -> None:
        self.kernel.interrupt(
            self.device_id,
            {"kind": "descriptor", "observed_at": self.kernel.clock()},
        )

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict) or raw.get("kind") != "descriptor":
            raise ValueError("descriptor driver accepts only descriptor frames")
        yield NormalizedObservation(
            self.path,
            self.descriptor,
            float(raw["observed_at"]),
            self.device_id,
        )

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        return "resource is read-only"

    def dispatch(self, operation: Operation) -> None:
        raise RuntimeError("resource is read-only")

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return Verification.pending()
