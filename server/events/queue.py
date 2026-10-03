"""Thread-safe queue primitives used by the autonomous event runtime."""

from __future__ import annotations

import collections
import threading
from typing import Any


QueuedEvent = tuple[dict[str, Any], int]


class EventQueue:
    """Priority-capable queue with a stable deque/condition interface.

    ``items`` remains public so the runtime can expose the same inspection
    seam used by diagnostics and tests while queue synchronization stays in
    one place.
    """

    def __init__(self) -> None:
        self.items: collections.deque[QueuedEvent] = collections.deque()
        self.condition = threading.Condition()

    def clear(self) -> None:
        with self.condition:
            self.items.clear()
            self.condition.notify_all()

    def enqueue(self, event: dict[str, Any], generation: int,
                *, priority: bool = False) -> None:
        with self.condition:
            item = (event, generation)
            if priority:
                self.items.appendleft(item)
            else:
                self.items.append(item)
            self.condition.notify()

    def wait_pop(self) -> QueuedEvent:
        with self.condition:
            while not self.items:
                self.condition.wait()
            return self.items.popleft()


__all__ = ["EventQueue", "QueuedEvent"]
