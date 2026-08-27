"""dos-backed adapter for the /api/domain/* surface the frontend consumes.

Duck-type compatible with server.domain_api.DomainApi so FloodApp can swap
it in unchanged.  Mapping:

- products    namespace paths /hydro/shanhu/forecasts/{id} and
              /hydro/shanhu/impacts/{id} become products with the legacy
              product types the frontend filters on
              (water.flood.forecast / water.flood.impact-assessment);
              impact products carry input_refs=[forecast_id] and
              data.parameters.time_h so the frontend predicate matches.
- events      journal records become an ordered event stream (cursor =
              journal seq); forecast/impact commits surface as
              *.generated events with product ids.
- stream      SSE over journal tail with heartbeat (same wire shape as
              legacy: domain_event / heartbeat frames).
- commands    consistency transactions; approvals parked as commands in
              state awaiting_approval.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator, Optional

from .serialization import format_sse

PROJECT_DIR = Path(__file__).resolve().parents[1]

FORECAST_TYPE = "water.flood.forecast"
IMPACT_TYPE = "water.flood.impact-assessment"
FORECASTS = "/hydro/shanhu/forecasts"
IMPACTS = "/hydro/shanhu/impacts"
SUBJECT = "water.watershed/shanhu"

# dos artifact keys → the legacy product-view names
_ARTIFACT_ALIASES = {"max_depth": "max_depth_csv", "depth_series": "depth_series", "time_steps": "time_steps"}


class DosRecordNotFound(KeyError):
    pass


class DosProductViews:
    """GIS compatibility views over dos forecast/impact namespace records.

    Reuses the kernel-agnostic mesh store and depth loaders; dos artifacts
    are the same files the legacy products pointed at."""

    native_forecasts = True

    def __init__(self, kernel):
        self.kernel = kernel
        from domains.flood.runtime.hydrodynamic_grid import STORE

        self.mesh = STORE

    # ------------------------------------------------------------ forecast

    def _resolve_forecast(self, product_id: str) -> tuple[str, dict[str, Any]]:
        selected = str(product_id or "latest").strip()
        if selected in ("", "latest"):
            snap = self.kernel.namespace.try_read(f"{FORECASTS}/latest")
            if snap is None or not (snap.value or {}).get("id"):
                raise DosRecordNotFound("no dos forecast available yet")
            selected = snap.value["id"]
        snap = self.kernel.namespace.try_read(f"{FORECASTS}/{selected}")
        if snap is None:
            raise DosRecordNotFound(f"unknown forecast: {selected}")
        return selected, snap.value

    def _artifacts(self, meta: dict) -> dict[str, str]:
        raw = meta.get("artifacts") or {}
        resolved = {}
        for legacy_name, dos_name in _ARTIFACT_ALIASES.items():
            reference = str(raw.get(dos_name) or raw.get(legacy_name) or "").strip()
            if reference:
                path = Path(reference)
                resolved[legacy_name] = str(path if path.is_absolute() else PROJECT_DIR / path)
        return resolved

    def _time_steps(self, meta: dict, artifacts: dict) -> list[float]:
        steps = (meta.get("stats") or {}).get("time_steps_h")
        if isinstance(steps, (list, tuple)) and steps:
            return [float(v) for v in steps]
        reference = artifacts.get("time_steps")
        if not reference:
            return []
        return [float(v) for v in (json.loads(Path(reference).read_text(encoding="utf-8")).get("time_steps_h") or [])]

    def _depths(self, meta: dict, artifacts: dict, requested_time_h: Optional[float]):
        from domains.flood.runtime.forecast import read_hydrodynamic_depth_csv

        if requested_time_h is None:
            path = artifacts.get("max_depth")
            if not path:
                raise DosRecordNotFound("forecast has no max_depth artifact")
            return read_hydrodynamic_depth_csv(Path(path)), None, None
        import numpy as np

        steps = self._time_steps(meta, artifacts)
        if not steps:
            raise ValueError("forecast has no time steps")
        requested = float(requested_time_h)
        if requested < 0:
            raise ValueError("time_h must not be negative")
        index = min(range(len(steps)), key=lambda i: abs(steps[i] - requested))
        series_path = artifacts.get("depth_series")
        if not series_path:
            raise DosRecordNotFound("forecast has no depth_series artifact")
        array = np.load(series_path, mmap_mode="r")
        values = np.asarray(array[index], dtype=np.float32)
        wet = np.flatnonzero(values > 0)
        return {int(i) + 1: float(values[i]) for i in wet}, steps[index], index

    def _result_version(self, forecast_id: str, artifacts: dict) -> str:
        parts = [forecast_id]
        for name in ("max_depth", "depth_series", "time_steps"):
            reference = artifacts.get(name)
            if not reference:
                parts.append(f"{name}:missing")
                continue
            try:
                stat = Path(reference).stat()
            except OSError:
                parts.append(f"{name}:missing")
            else:
                parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(parts)

    def _metadata(self, forecast_id: str, meta: dict, artifacts: dict, depths: dict) -> dict[str, Any]:
        from datetime import datetime, timezone

        valid_from = meta.get("valid_from")
        valid_from_iso = (
            datetime.fromtimestamp(float(valid_from), tz=timezone.utc).isoformat() if valid_from else None
        )
        steps = self._time_steps(meta, artifacts)
        return {
            "forecast_id": forecast_id,
            "forecast_version": forecast_id,
            "forecast_time": valid_from_iso,
            "valid_from": valid_from_iso,
            "generated_at": (meta.get("filed_at") or meta.get("generated_at")),
            "lead_time_h": (meta.get("input") or {}).get("window_hours"),
            "rainfall_series": [],
            "result_version": self._result_version(forecast_id, artifacts),
            "depth_path": artifacts.get("max_depth", ""),
            "series_path": artifacts.get("depth_series", ""),
            "depth_count": len(depths),
            "flooded_count": sum(d > 0 for d in depths.values()),
            "max_depth_m": round(max(depths.values(), default=0.0), 4),
            "time_steps_h": steps,
            "time_steps": [{"time_h": t, "valid_at": None} for t in steps],
            "time_step_count": len(steps),
            "product_type": FORECAST_TYPE,
            "producer_id": "dos:compute:flood-cnn-v2",
        }

    def forecast_grid_meta(self, product_id: str) -> dict[str, Any]:
        forecast_id, meta = self._resolve_forecast(product_id)
        artifacts = self._artifacts(meta)
        depths, _, _ = self._depths(meta, artifacts, None)
        return self.mesh.meta_from_depths(self._metadata(forecast_id, meta, artifacts, depths), depths)

    def forecast_grid_tile(
        self, z: int, x: int, y: int, product_id: str, *, wet_only: bool = False, time_h: Optional[float] = None, tile_crs: str = "wgs84",
    ) -> dict[str, Any]:
        forecast_id, meta = self._resolve_forecast(product_id)
        artifacts = self._artifacts(meta)
        depths, actual_time_h, time_index = self._depths(meta, artifacts, time_h)
        return self.mesh.tile_from_depths(
            z, x, y, depths,
            source_id=forecast_id,
            result_version=self._result_version(forecast_id, artifacts),
            wet_only=wet_only, time_h=actual_time_h, time_index=time_index, tile_crs=tile_crs,
        )

    def impact_for_forecast(self, forecast_product_id: str, **_) -> dict[str, Any]:
        for path in reversed(self.kernel.namespace.paths(IMPACTS)):
            impact_id = path.rsplit("/", 1)[-1]
            if not impact_id.startswith("impact_"):
                continue
            snap = self.kernel.namespace.try_read(path)
            if snap is not None and snap.value.get("forecast_id") == forecast_product_id:
                return self.impact_assessment(impact_id)
        raise DosRecordNotFound(f"no impact assessment for forecast: {forecast_product_id}")

    # -------------------------------------------------------------- impact

    def impact_assessment(self, product_id: str) -> dict[str, Any]:
        snap = self.kernel.namespace.try_read(f"{IMPACTS}/{product_id}")
        if snap is None:
            raise DosRecordNotFound(f"unknown impact assessment: {product_id}")
        meta = snap.value
        result = dict(meta.get("summary") or {})
        result.update(
            {
                "targets": meta.get("targets") or [],
                "highlights": meta.get("highlights") or [],
                "artifacts": meta.get("artifacts") or {},
                "assessment_product_id": product_id,
                "forecast_product_id": meta.get("forecast_id"),
                "forecast_id": meta.get("forecast_id"),
                "generated_at": meta.get("filed_at"),
                "input_refs": [meta.get("forecast_id")] if meta.get("forecast_id") else [],
                "parameters": {"time_h": None, "target_type": "all"},
            }
        )
        return result


class DosApi:
    domain_id = "water.flood"
    native_forecasts = True  # flood_app routes forecast ids here when set

    def __init__(self, host) -> None:
        self.host = host
        self.kernel = host.kernel
        self.views = DosProductViews(host.kernel)

    def close(self) -> None:
        self.host.stop()

    # ------------------------------------------------------------- products

    def _products(self) -> list[dict[str, Any]]:
        items = []
        for path in self.kernel.namespace.paths(FORECASTS):
            forecast_id = path.rsplit("/", 1)[-1]
            if not forecast_id.startswith("fcst_"):
                continue
            meta = self.kernel.namespace.try_read(path)
            if meta is None:
                continue
            value = meta.value
            items.append(
                {
                    "product_id": forecast_id,
                    "product_type": FORECAST_TYPE,
                    "subject_id": SUBJECT,
                    "generated_at": value.get("valid_from"),
                    "input_refs": [],
                    "data": {
                        "stats": value.get("stats") or {},
                        "input": value.get("input") or {},
                        "artifacts": value.get("artifacts") or {},
                        "parameters": {"time_h": None},
                    },
                }
            )
        for path in self.kernel.namespace.paths(IMPACTS):
            impact_id = path.rsplit("/", 1)[-1]
            if not impact_id.startswith("impact_"):
                continue
            meta = self.kernel.namespace.try_read(path)
            if meta is None:
                continue
            value = meta.value
            items.append(
                {
                    "product_id": impact_id,
                    "product_type": IMPACT_TYPE,
                    "subject_id": SUBJECT,
                    "generated_at": value.get("filed_at") or meta.ts,
                    "input_refs": [value.get("forecast_id")],
                    "data": {
                        "summary": value.get("summary") or {},
                        "highlights": value.get("highlights") or [],
                        "artifacts": value.get("artifacts") or {},
                        "parameters": {"time_h": None},
                    },
                }
            )
        items.sort(key=lambda item: item["product_id"])
        return items

    def products(self, *, product_type: Optional[str] = None, subject_id: Optional[str] = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        items = [p for p in self._products() if product_type in (None, p["product_type"]) and subject_id in (None, p["subject_id"])]
        return {
            "domain_id": self.domain_id,
            "total": len(items),
            "offset": int(offset),
            "limit": int(limit),
            "items": items[int(offset) : int(offset) + int(limit)],
        }

    def product(self, product_id: str) -> dict[str, Any]:
        for item in self._products():
            if item["product_id"] == product_id:
                return item
        raise KeyError(product_id)

    # --------------------------------------------------------------- events

    def _event_for(self, record) -> Optional[dict[str, Any]]:
        payload = record.payload
        if record.kind == "observation":
            path = payload.get("path", "")
            if path.startswith(f"{FORECASTS}/fcst_") and path.count("/") == 4:
                return {
                    "event_type": f"{FORECAST_TYPE}.generated",
                    "subject_id": SUBJECT,
                    "occurred_at": payload.get("observed_at", record.ts),
                    "data": {"product_id": path.rsplit("/", 1)[-1]},
                }
            if path.startswith(f"{IMPACTS}/impact_") and path.count("/") == 4:
                impact_id = path.rsplit("/", 1)[-1]
                meta = self.kernel.namespace.try_read(path)
                forecast_id = (meta.value or {}).get("forecast_id") if meta else None
                return {
                    "event_type": f"{IMPACT_TYPE}.generated",
                    "subject_id": SUBJECT,
                    "occurred_at": payload.get("observed_at", record.ts),
                    "data": {"product_id": impact_id, "input_refs": [forecast_id] if forecast_id else []},
                }
        if record.kind == "txn" and payload.get("event") == "awaiting_approval":
            return {
                "event_type": "water.command.approval_required",
                "subject_id": payload.get("path"),
                "occurred_at": record.ts,
                "data": {"command_id": payload.get("txn_id"), "action": payload.get("action")},
            }
        return None

    def events(self, *, after: int = 0, event_type: Optional[str] = None, subject_id: Optional[str] = None, limit: int = 100) -> dict[str, Any]:
        items = []
        for record in self.kernel.journal.tail(int(after)):
            event = self._event_for(record)
            if event is None:
                continue
            if event_type is not None and event["event_type"] != event_type:
                continue
            if subject_id is not None and event["subject_id"] != subject_id:
                continue
            items.append({"cursor": record.seq, "event": event})
            if len(items) >= int(limit):
                break
        return {
            "domain_id": self.domain_id,
            "items": items,
            "next_cursor": self.kernel.journal.last_seq,
            "head_cursor": self.kernel.journal.last_seq,
            "total": len(items),
        }

    def stream_events(self, *, after: int = 0, event_type: Optional[str] = None, subject_id: Optional[str] = None, heartbeat_seconds: float = 15.0) -> Iterator[bytes]:
        cursor = int(after)
        while True:
            result = self.events(after=cursor, event_type=event_type, subject_id=subject_id, limit=100)
            if not result["items"]:
                yield format_sse(
                    "heartbeat",
                    {"type": "heartbeat", "domain_id": self.domain_id, "cursor": cursor},
                    event_id=str(cursor),
                )
                deadline = time.time() + heartbeat_seconds
                while time.time() < deadline and self.kernel.journal.last_seq <= cursor:
                    time.sleep(0.25)
                continue
            for item in result["items"]:
                cursor = int(item["cursor"])
                yield format_sse(
                    "domain_event",
                    {"type": "domain_event", "domain_id": self.domain_id, "cursor": cursor, **item["event"]},
                    event_id=str(cursor),
                )

    # ------------------------------------------------------------- commands

    def commands(self, *, state: Optional[str] = None, resource_id: Optional[str] = None, actor_id: Optional[str] = None, capability_id: Optional[str] = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        del resource_id, actor_id, capability_id  # filters preserved for shape parity
        items = []
        for txn in list(self.kernel.consistency._pending.values()):
            item = {
                "command_id": txn.txn_id,
                "state": _command_state(txn.state),
                "resource_id": txn.path,
                "capability_id": txn.action,
                "arguments": txn.args,
                "error": txn.error,
                "requested_at": None,
            }
            if state is not None and item["state"] != state:
                continue
            items.append(item)
        items.sort(key=lambda c: c["command_id"])
        return {
            "domain_id": self.domain_id,
            "total": len(items),
            "offset": int(offset),
            "limit": int(limit),
            "items": items[int(offset) : int(offset) + int(limit)],
        }

    def command(self, command_id: str) -> dict[str, Any]:
        txn = self.kernel.consistency.find(command_id)
        if txn is None:
            raise KeyError(command_id)
        return {
            "command_id": txn.txn_id,
            "state": _command_state(txn.state),
            "resource_id": txn.path,
            "capability_id": txn.action,
            "arguments": txn.args,
            "error": txn.error,
        }

    # --------------------------------------------------------- projections

    def projections(self, *, resource_id: Optional[str] = None, resource_type: Optional[str] = None) -> dict[str, Any]:
        del resource_type
        paths = self.kernel.namespace.paths(resource_id if resource_id else "/")
        return {
            "domain_id": self.domain_id,
            "items": [
                {"resource_id": p, "projection": self.kernel.namespace.read(p).value}
                for p in paths
                if self.kernel.namespace.try_read(p) is not None and not isinstance(self.kernel.namespace.read(p).value, dict)
            ][:200],
            "total": len(paths),
        }


def _command_state(txn_state: str) -> str:
    return {
        "open": "submitted",
        "awaiting_approval": "pending_approval",
        "dispatched": "acknowledged",
        "committed": "confirmed",
        "failed": "failed",
        "unknown": "outcome_unknown",
    }.get(txn_state, txn_state)
