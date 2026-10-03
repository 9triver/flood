"""Concurrent cache for hydrodynamic depth results."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable


DEPTH_CACHE_MAX = 8
DEPTH_CACHE_LOCK = threading.Lock()
DEPTH_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
DEPTH_LOADS: dict[tuple[Any, ...], "DepthLoad"] = {}


class DepthLoad:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.entry: dict[str, Any] | None = None
        self.error: BaseException | None = None


def cached_depth_entry(
    cache_key: tuple[Any, ...],
    stat_key: Any,
    loader: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    with DEPTH_CACHE_LOCK:
        cached = DEPTH_CACHE.get(cache_key)
        if cached is not None and cached.get("stat_key") == stat_key:
            DEPTH_CACHE.move_to_end(cache_key)
            return cached
        load = DEPTH_LOADS.get(cache_key)
        is_loader = load is None
        if load is None:
            load = DepthLoad()
            DEPTH_LOADS[cache_key] = load

    if not is_loader:
        load.event.wait()
        if load.error is not None:
            raise load.error
        if load.entry is None:
            raise RuntimeError("depth cache load completed without a result")
        return load.entry

    try:
        entry = loader()
        with DEPTH_CACHE_LOCK:
            cached = DEPTH_CACHE.get(cache_key)
            if cached is not None and cached.get("stat_key") == stat_key:
                DEPTH_CACHE.move_to_end(cache_key)
                result = cached
            else:
                result = cache_depth_entry(cache_key, entry)
            load.entry = result
            load.event.set()
            if DEPTH_LOADS.get(cache_key) is load:
                del DEPTH_LOADS[cache_key]
            return result
    except BaseException as error:
        with DEPTH_CACHE_LOCK:
            load.error = error
            load.event.set()
            if DEPTH_LOADS.get(cache_key) is load:
                del DEPTH_LOADS[cache_key]
        raise


def cache_depth_entry(cache_key: tuple[Any, ...],
                      entry: dict[str, Any]) -> dict[str, Any]:
    DEPTH_CACHE[cache_key] = entry
    DEPTH_CACHE.move_to_end(cache_key)
    while len(DEPTH_CACHE) > DEPTH_CACHE_MAX:
        DEPTH_CACHE.popitem(last=False)
    return entry


__all__ = [
    "DEPTH_CACHE_LOCK",
    "DEPTH_CACHE",
    "cached_depth_entry",
    "cache_depth_entry",
]
