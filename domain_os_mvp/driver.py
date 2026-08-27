from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from .model import NormalizedObservation, Observation, Operation, Verification


class Driver(ABC):
    """Minimal device contract.

    ``dispatch`` sends a command but must not wait for the resulting world
    change. ``verify`` decides the operation only from observations recorded
    after the dispatch fence.
    """

    device_id: str = ""
    privileged_actions: frozenset[str] = frozenset()
    operation_timeout_seconds: float | None = 30.0
    kernel = None

    def attach(self, kernel) -> None:
        self.kernel = kernel

    @abstractmethod
    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        pass

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        return None

    @abstractmethod
    def dispatch(self, operation: Operation) -> None:
        pass

    @abstractmethod
    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        pass

    def deadline_for(self, action: str) -> float | None:
        return self.operation_timeout_seconds
