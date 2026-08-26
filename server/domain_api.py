"""HTTP-neutral facade for the Domain OS query and GIS compatibility APIs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from domain_os import DomainQueryService
from domains.flood.product_views import FloodProductViews

from .serialization import format_sse


class DomainApiUnavailable(RuntimeError):
    pass


class DomainApi:
    def __init__(self, queries: DomainQueryService) -> None:
        self.queries = queries
        self.views = FloodProductViews(queries)

    def close(self) -> None:
        self.queries.close()

    def projections(
        self,
        *,
        resource_id: str | None = None,
        resource_type: str | None = None,
    ) -> dict[str, Any]:
        return self.queries.projections(
            resource_id=resource_id,
            resource_type=resource_type,
        )

    def products(
        self,
        *,
        product_type: str | None = None,
        subject_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.queries.products(
            product_type=product_type,
            subject_id=subject_id,
            offset=offset,
            limit=limit,
        )

    def product(self, product_id: str) -> dict[str, Any]:
        return self.queries.product(product_id)

    def commands(
        self,
        *,
        state: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        capability_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.queries.commands(
            state=state,
            resource_id=resource_id,
            actor_id=actor_id,
            capability_id=capability_id,
            offset=offset,
            limit=limit,
        )

    def command(self, command_id: str) -> dict[str, Any]:
        return self.queries.command(command_id)

    def events(
        self,
        *,
        after: int = 0,
        event_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.queries.events(
            after=after,
            event_type=event_type,
            subject_id=subject_id,
            limit=limit,
        )

    def stream_events(
        self,
        *,
        after: int = 0,
        event_type: str | None = None,
        subject_id: str | None = None,
        heartbeat_seconds: float = 15.0,
    ) -> Iterator[bytes]:
        cursor = int(after)
        while True:
            result = self.queries.wait_for_events(
                after=cursor,
                event_type=event_type,
                subject_id=subject_id,
                timeout=heartbeat_seconds,
            )
            cursor = int(result["next_cursor"])
            if not result["items"]:
                yield format_sse(
                    "heartbeat",
                    {
                        "type": "heartbeat",
                        "domain_id": self.queries.domain_id,
                        "cursor": cursor,
                    },
                    event_id=str(cursor),
                )
                continue
            for item in result["items"]:
                cursor = int(item["cursor"])
                yield format_sse(
                    "domain_event",
                    {
                        "type": "domain_event",
                        "domain_id": self.queries.domain_id,
                        "cursor": cursor,
                        **item["event"],
                    },
                    event_id=str(cursor),
                )
