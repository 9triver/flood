from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel import Kernel


class ProcessContext:
    def __init__(self, kernel: "Kernel"):
        self._kernel = kernel

    def read(self, path: str):
        return self._kernel.read(path)

    def act(
        self,
        capability_token: str,
        path: str,
        action: str,
        arguments: dict | None = None,
        *,
        expected_revision: int | None = None,
    ):
        return self._kernel.act(
            capability_token,
            path,
            action,
            arguments,
            expected_revision=expected_revision,
        )


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    watches: tuple[str, ...]
    handler: Callable[[ProcessContext], None]
