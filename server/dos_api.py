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

import time
from typing import Any, Iterator, Optional

from .serialization import format_sse

FORECAST_TYPE = "water.flood.forecast"
IMPACT_TYPE = "water.flood.impact-assessment"
FORECASTS = "/hydro/shanhu/forecasts"
IMPACTS = "/hydro/shanhu/impacts"
SUBJECT = "water.watershed/shanhu"


class DosApi:
    domain_id = "water.flood"

    def __init__(self, host) -> None:
        self.host = host
        self.kernel = host.kernel

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
