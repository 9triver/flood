from __future__ import annotations

import unittest

from domain_os import (
    DomainControlConflict,
    DomainControlService,
    InMemoryMqttTransport,
)
from domains.flood.domain_system import (
    SET_SAMPLING_INTERVAL,
    create_flood_domain_system,
    station_resource_id,
)


STATION_ID = "808J1510"


class DomainControlServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.transport = InMemoryMqttTransport()
        self.system = create_flood_domain_system(
            mqtt_transport=self.transport,
            station_ids=(STATION_ID,),
        )
        await self.system.start()
        self.control = DomainControlService(self.system.runtime)

    async def asyncTearDown(self) -> None:
        await self.system.stop()

    def intent_payload(self) -> dict:
        return {
            "intent_id": "intent-http-1",
            "actor_id": "agent.http-test",
            "resource_id": station_resource_id(STATION_ID),
            "capability_id": SET_SAMPLING_INTERVAL,
            "arguments": {"seconds": 10},
            "rationale": "Increase observation frequency",
            "correlation_id": "http-flood-episode",
        }

    async def test_submit_retry_and_reject_are_governed(self):
        pending = await self.control.submit_intent(self.intent_payload())
        repeated = await self.control.submit_intent(self.intent_payload())

        self.assertEqual("pending_approval", pending["state"])
        self.assertEqual(pending["command_id"], repeated["command_id"])
        self.assertEqual([], self.transport.published)

        rejected = await self.control.reject(pending["command_id"], {
            "rejector_id": "operator-002",
            "reason": "现场检修",
        })
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("operator-002", rejected["rejected_by"])
        self.assertEqual("现场检修", rejected["rejection_reason"])

    async def test_same_intent_id_cannot_change_request(self):
        await self.control.submit_intent(self.intent_payload())
        changed = self.intent_payload()
        changed["arguments"] = {"seconds": 20}

        with self.assertRaisesRegex(
            DomainControlConflict,
            "different content",
        ):
            await self.control.submit_intent(changed)

    async def test_approval_dispatches_but_cannot_be_repeated(self):
        pending = await self.control.submit_intent(self.intent_payload())

        acknowledged = await self.control.approve(pending["command_id"], {
            "approver_id": "operator-001",
        })

        self.assertEqual("acknowledged", acknowledged["state"])
        self.assertEqual("operator-001", acknowledged["approved_by"])
        self.assertEqual(1, len(self.transport.published))
        with self.assertRaisesRegex(
            DomainControlConflict,
            "not pending approval",
        ):
            await self.control.approve(pending["command_id"], {
                "approver_id": "operator-001",
            })

    async def test_unknown_fields_and_naive_timestamps_are_rejected(self):
        unknown = self.intent_payload()
        unknown["protocol_topic"] = "must-not-cross-domain-boundary"
        with self.assertRaisesRegex(ValueError, "unknown request fields"):
            await self.control.submit_intent(unknown)

        naive = self.intent_payload()
        naive["requested_at"] = "2026-08-26T09:00:00"
        with self.assertRaisesRegex(ValueError, "include a timezone"):
            await self.control.submit_intent(naive)


if __name__ == "__main__":
    unittest.main()
