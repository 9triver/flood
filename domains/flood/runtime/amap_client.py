"""Small adapter around the AMap Web Service routing API.

Route safety decisions remain in ``route_planning``.  This adapter only builds
requests and normalizes transport/API failures into ``RoutingEngineError``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .coordinates import wgs84_to_gcj02
from .config import runtime_setting


DEFAULT_AMAP_URL = "https://restapi.amap.com"


class RoutingEngineError(RuntimeError):
    def __init__(self, message: str, status: str = "routing_engine_unavailable",
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


def amap_request(start: tuple[float, float], destination: tuple[float, float],
                 profile: str) -> dict[str, Any]:
    if profile not in {"car", "foot"}:
        raise RoutingEngineError(
            f"高德路线暂不支持 profile={profile}，可选 car/foot。",
            status="routing_request_invalid",
        )
    origin = wgs84_to_gcj02(*start)
    target = wgs84_to_gcj02(*destination)
    endpoint = "/v5/direction/walking" if profile == "foot" else "/v3/direction/driving"
    params = {
        "origin": format_coordinate(origin),
        "destination": format_coordinate(target),
        "output": "json",
    }
    if profile == "foot":
        params["alternative_route"] = "3"
        params["show_fields"] = "cost,polyline"
    else:
        params["strategy"] = "0"
        params["extensions"] = "base"
    return {"endpoint": endpoint, "profile": profile, "params": params}


def call_amap(api_key: str, payload: dict[str, Any],
              timeout_seconds: float) -> dict[str, Any]:
    base_url = runtime_setting("AMAP_WEB_SERVICE_URL", DEFAULT_AMAP_URL)
    endpoint = f"{base_url.rstrip('/')}{payload['endpoint']}"
    params = {**payload["params"], "key": api_key}
    request = Request(
        f"{endpoint}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "flood-routing/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RoutingEngineError(
            f"高德路线接口返回 HTTP {exc.code}: {detail[:800]}"
        ) from exc
    except URLError as exc:
        raise RoutingEngineError(
            f"无法连接高德路线接口 {endpoint}: {exc.reason}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingEngineError(f"高德路线响应不可用: {exc}") from exc
    if not isinstance(result, dict):
        raise RoutingEngineError("高德路线接口返回了无效响应。")
    if str(result.get("status")) != "1":
        raise RoutingEngineError(
            f"高德路线接口调用失败: {result.get('info') or 'unknown'} "
            f"({result.get('infocode') or 'unknown'})",
            status=(
                "routing_request_invalid"
                if str(result.get("infocode", "")).startswith("10")
                else "no_safe_route"
            ),
        )
    return result


def format_coordinate(point: tuple[float, float]) -> str:
    return f"{point[0]:.6f},{point[1]:.6f}"


__all__ = [
    "DEFAULT_AMAP_URL",
    "RoutingEngineError",
    "amap_request",
    "call_amap",
    "format_coordinate",
]
