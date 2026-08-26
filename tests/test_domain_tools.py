from __future__ import annotations

import json
import unittest

from oag.tools.registry import ToolRegistry

from server.domain_tools import (
    DOMAIN_QUERY_TOOL_NAMES,
    build_domain_event_context,
    register_domain_query_tools,
)


class StubQueries:
    domain_id = "water.flood"

    def __init__(self) -> None:
        self.products_by_id = {
            "water.flood.forecast/run/000001": {
                "product_id": "water.flood.forecast/run/000001",
                "product_type": "water.flood.forecast",
                "subject_id": "water.watershed/shanhu",
                "producer_id": "water.model/flood-cnn-v2",
                "generated_at": "2026-08-26T01:00:00+00:00",
                "valid_from": "2026-08-26T01:00:00+00:00",
                "valid_to": "2026-08-27T01:00:00+00:00",
                "input_refs": ["water.flood.forecast-input/run/000001"],
                "data": {
                    "status": "completed",
                    "forecast_cell_count": 10330,
                    "time_steps_h": list(range(48)),
                },
                "artifacts": {"max_depth": "/runtime/max_depth.csv"},
                "correlation_id": "flood-run",
                "causation_id": "command-1",
            },
        }
        self.events_by_id = {
            "event-1": {
                "event_id": "event-1",
                "event_type": "water.flood.forecast.generated",
                "subject_id": "water.watershed/shanhu",
                "occurred_at": "2026-08-26T01:01:00+00:00",
                "data": {"product_id": "water.flood.forecast/run/000001"},
                "correlation_id": "flood-run",
                "causation_id": "command-1",
            },
        }
        self.commands_by_id = {
            "command-1": {
                "command_id": "command-1",
                "intent": {
                    "intent_id": "intent-1",
                    "actor_id": "water.rule.forecast-trigger",
                    "resource_id": "water.model/flood-cnn-v2",
                    "capability_id": "water.flood.run-forecast",
                    "arguments": {
                        "input_product_id": (
                            "water.flood.forecast-input/run/000001"
                        ),
                    },
                    "requested_at": "2026-08-26T01:00:00+00:00",
                    "rationale": "Deterministic trigger",
                    "correlation_id": "flood-run",
                },
                "driver_id": "water.infrastructure.flood-model",
                "state": "confirmed",
                "created_at": "2026-08-26T01:00:00+00:00",
                "updated_at": "2026-08-26T01:01:00+00:00",
                "policy_reason": "low risk",
                "approved_by": None,
                "rejected_by": None,
                "rejection_reason": None,
                "dispatched_at": "2026-08-26T01:00:01+00:00",
                "external_id": "cnn-command-1",
                "expected_state": {},
                "output": {
                    "product_id": "water.flood.forecast/run/000001",
                },
                "error": None,
            },
        }

    def projections(self, **filters):
        return {
            "domain_id": self.domain_id,
            "items": [{
                "resource": {"resource_id": filters.get("resource_id") or "station-1"},
                "values": {"water_level_m": {"value": 246.8, "unit": "m"}},
            }],
            "count": 1,
        }

    def products(self, **filters):
        del filters
        items = list(self.products_by_id.values())
        return {
            "domain_id": self.domain_id,
            "items": items,
            "count": len(items),
            "total": len(items),
            "offset": 0,
            "limit": 20,
        }

    def product(self, product_id):
        if product_id not in self.products_by_id:
            from domain_os import DomainRecordNotFound
            raise DomainRecordNotFound(f"unknown product: {product_id}")
        return self.products_by_id[product_id]

    def events(self, **filters):
        del filters
        event = self.events_by_id["event-1"]
        return {
            "domain_id": self.domain_id,
            "items": [{"cursor": 9, "event": event}],
            "count": 1,
            "after": 0,
            "next_cursor": 9,
            "head_cursor": 9,
            "limit": 20,
        }

    def event(self, event_id):
        if event_id not in self.events_by_id:
            from domain_os import DomainRecordNotFound
            raise DomainRecordNotFound(f"unknown event: {event_id}")
        return self.events_by_id[event_id]

    def commands(self, **filters):
        del filters
        items = list(self.commands_by_id.values())
        return {
            "domain_id": self.domain_id,
            "items": items,
            "count": len(items),
            "total": len(items),
            "offset": 0,
            "limit": 20,
        }

    def command(self, command_id):
        if command_id not in self.commands_by_id:
            from domain_os import DomainRecordNotFound
            raise DomainRecordNotFound(f"unknown command: {command_id}")
        return self.commands_by_id[command_id]


class DomainToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.queries = StubQueries()
        self.tools = ToolRegistry()
        register_domain_query_tools(self.tools, lambda: self.queries)

    def test_registers_read_only_domain_query_tools(self):
        self.assertEqual(
            DOMAIN_QUERY_TOOL_NAMES,
            frozenset(tool["function"]["name"] for tool in self.tools.build_tools()),
        )
        self.assertTrue(all(
            self.tools.get(name).policy.read_only
            for name in DOMAIN_QUERY_TOOL_NAMES
        ))

    def test_product_tools_preserve_identity_validity_and_lineage(self):
        listed = json.loads(self.tools.get("domain_list_products").handler({}))
        product = listed["items"][0]

        self.assertEqual("water.flood.forecast/run/000001", product["product_id"])
        self.assertEqual("2026-08-27T01:00:00+00:00", product["valid_to"])
        self.assertEqual(
            ["water.flood.forecast-input/run/000001"],
            product["input_refs"],
        )
        self.assertEqual(["max_depth"], product["artifact_names"])

        detail = json.loads(self.tools.get("domain_get_product").handler({
            "product_id": product["product_id"],
        }))
        self.assertEqual(48, detail["data"]["time_steps_h"]["item_count"])
        self.assertEqual(30, len(detail["data"]["time_steps_h"]["preview"]))

    def test_event_context_resolves_explicit_ids_without_copying_payload(self):
        context = build_domain_event_context(self.queries, {
            "event_id": "event-1",
            "payload": {
                "forecast_product_id": "water.flood.forecast/run/000001",
            },
        })

        self.assertEqual("explicit", context["linkage"])
        self.assertEqual(
            "water.flood.forecast/run/000001",
            context["product_refs"][0]["product_id"],
        )
        self.assertEqual("event-1", context["event_refs"][0]["event_id"])
        self.assertNotIn("data", context["product_refs"][0])

    def test_event_context_marks_unlinked_legacy_event(self):
        context = build_domain_event_context(self.queries, {
            "event_id": "evt_legacy",
            "payload": {"forecast_id": "latest"},
        })

        self.assertEqual("unlinked_legacy_event", context["linkage"])
        self.assertEqual([], context["product_refs"])
        self.assertIn("不得关联", context["reference_policy"])

    def test_command_tools_and_event_context_preserve_control_state(self):
        commands = json.loads(self.tools.get("domain_list_commands").handler({
            "state": "confirmed",
        }))
        command = json.loads(self.tools.get("domain_get_command").handler({
            "command_id": "command-1",
        }))
        context = build_domain_event_context(self.queries, {
            "event_id": "evt_control",
            "payload": {"command_id": "command-1"},
        })

        self.assertEqual("command-1", commands["items"][0]["command_id"])
        self.assertEqual("confirmed", command["state"])
        self.assertEqual("explicit", context["linkage"])
        self.assertEqual("command-1", context["command_refs"][0]["command_id"])

    def test_tool_returns_model_readable_error(self):
        result = json.loads(self.tools.get("domain_get_product").handler({
            "product_id": "missing",
        }))

        self.assertEqual("DomainRecordNotFound", result["error_type"])
        self.assertIn("unknown product", result["error"])


if __name__ == "__main__":
    unittest.main()
