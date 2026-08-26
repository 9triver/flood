from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from domain_os import (
    Command,
    CommandState,
    InMemoryMqttTransport,
    Intent,
    PolicyDecision,
    SqliteDomainStore,
    new_id,
    utc_now,
)
from domains.flood.domain_system import (
    DOMAIN_ID,
    MQTT_DRIVER_ID,
    SET_SAMPLING_INTERVAL,
    create_flood_domain_system,
    station_resource_id,
)


STATION_ID = "808J1510"
RESOURCE_ID = station_resource_id(STATION_ID)


def telemetry(
    *,
    message_id: str,
    observed_at: str,
    sequence: int,
    metrics: dict,
    quality: str = "good",
) -> bytes:
    return json.dumps({
        "message_id": message_id,
        "observed_at": observed_at,
        "sequence": sequence,
        "quality": quality,
        "metrics": metrics,
    }).encode("utf-8")


class AllowPolicy:
    def evaluate(self, intent, resource, capability):
        return PolicyDecision(allowed=True, reason="test policy permits action")


class RetainedTelemetryTransport(InMemoryMqttTransport):
    async def subscribe(self, topic_filter, handler):
        await super().subscribe(topic_filter, handler)
        await handler(
            f"water/stations/{STATION_ID}/telemetry",
            telemetry(
                message_id="retained-on-subscribe",
                observed_at="2026-08-26T09:00:00+08:00",
                sequence=1,
                metrics={"water_level_m": {"value": 246.3, "unit": "m"}},
            ),
        )


class DomainOSWaterVerticalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = InMemoryMqttTransport()
        self.system = create_flood_domain_system(
            mqtt_transport=self.transport,
            station_ids=(STATION_ID,),
        )
        await self.system.start()

    async def asyncTearDown(self):
        await self.system.stop()

    async def test_mqtt_observations_update_the_authoritative_projection(self):
        await self.transport.inject(
            f"water/stations/{STATION_ID}/telemetry",
            telemetry(
                message_id="telemetry-1",
                observed_at="2026-08-26T09:00:00+08:00",
                sequence=1,
                metrics={
                    "water_level_m": {"value": 246.3, "unit": "m"},
                    "sampling_interval_seconds": {"value": 60, "unit": "s"},
                },
            ),
        )

        projection = self.system.runtime.projection(RESOURCE_ID)
        self.assertEqual(246.3, projection["water_level_m"].value)
        self.assertEqual("m", projection["water_level_m"].unit)
        self.assertEqual(60, projection["sampling_interval_seconds"].value)
        self.assertEqual(2, len(self.system.runtime.observations()))
        self.assertEqual(
            2,
            len(self.system.runtime.events(event_type="domain.projection.updated")),
        )

    async def test_late_and_bad_data_remain_facts_but_do_not_rewind_state(self):
        topic = f"water/stations/{STATION_ID}/telemetry"
        await self.transport.inject(
            topic,
            telemetry(
                message_id="current",
                observed_at="2026-08-26T10:00:00+08:00",
                sequence=2,
                metrics={"water_level_m": {"value": 246.8, "unit": "m"}},
            ),
        )
        await self.transport.inject(
            topic,
            telemetry(
                message_id="late",
                observed_at="2026-08-26T09:00:00+08:00",
                sequence=1,
                metrics={"water_level_m": {"value": 245.1, "unit": "m"}},
            ),
        )
        await self.transport.inject(
            topic,
            telemetry(
                message_id="bad",
                observed_at="2026-08-26T11:00:00+08:00",
                sequence=3,
                quality="bad",
                metrics={"water_level_m": {"value": -999, "unit": "m"}},
            ),
        )

        self.assertEqual(3, len(self.system.runtime.observations()))
        self.assertEqual(
            246.8,
            self.system.runtime.projection(RESOURCE_ID)["water_level_m"].value,
        )

    async def test_agent_intent_requires_approval_and_telemetry_confirmation(self):
        topic = f"water/stations/{STATION_ID}/telemetry"
        await self.transport.inject(
            topic,
            telemetry(
                message_id="before-command",
                observed_at="2026-08-26T09:00:00+08:00",
                sequence=1,
                metrics={"sampling_interval_seconds": {"value": 60, "unit": "s"}},
            ),
        )

        pending = await self.system.request_sampling_interval(
            actor_id="agent.water-observer",
            station_id=STATION_ID,
            seconds=10,
            rationale="Increase observation frequency during rapid water-level rise",
            correlation_id="flood-episode-1",
        )

        self.assertEqual(CommandState.PENDING_APPROVAL, pending.state)
        self.assertEqual(SET_SAMPLING_INTERVAL, pending.intent.capability_id)
        self.assertEqual([], self.transport.published)

        acknowledged = await self.system.runtime.approve(
            pending.command_id,
            approver_id="operator-001",
        )
        self.assertEqual(CommandState.ACKNOWLEDGED, acknowledged.state)
        self.assertEqual(1, len(self.transport.published))
        published = self.transport.published[0]
        self.assertEqual(
            f"water/stations/{STATION_ID}/commands",
            published.topic,
        )
        self.assertEqual(1, published.qos)
        self.assertEqual(10, json.loads(published.payload)["seconds"])

        await self.transport.inject(
            topic,
            telemetry(
                message_id="after-command",
                observed_at="2026-08-26T09:01:00+08:00",
                sequence=2,
                metrics={"sampling_interval_seconds": {"value": 10, "unit": "s"}},
            ),
        )

        confirmed = self.system.runtime.command(pending.command_id)
        self.assertEqual(CommandState.CONFIRMED, confirmed.state)
        self.assertEqual("operator-001", confirmed.approved_by)
        self.assertEqual(
            "flood-episode-1",
            self.system.runtime.events(event_type="domain.command.confirmed")[0].correlation_id,
        )

    async def test_duplicate_telemetry_is_idempotent(self):
        payload = telemetry(
            message_id="duplicate",
            observed_at="2026-08-26T09:00:00+08:00",
            sequence=1,
            metrics={"water_level_m": {"value": 246.3, "unit": "m"}},
        )
        topic = f"water/stations/{STATION_ID}/telemetry"

        await self.transport.inject(topic, payload)
        await self.transport.inject(topic, payload)

        self.assertEqual(1, len(self.system.runtime.observations()))
        self.assertEqual(1, len(self.system.runtime.events(
            event_type="domain.observation.recorded"
        )))

    async def test_duplicate_intent_id_is_idempotent_but_cannot_change_content(self):
        intent = Intent(
            intent_id="intent-idempotent-1",
            actor_id="agent.water-observer",
            resource_id=RESOURCE_ID,
            capability_id=SET_SAMPLING_INTERVAL,
            arguments={"seconds": 10},
            requested_at=utc_now(),
            rationale="Increase observation frequency",
        )

        first = await self.system.runtime.submit_intent(intent)
        repeated = await self.system.runtime.submit_intent(intent)

        self.assertEqual(first, repeated)
        self.assertEqual(1, len(self.system.runtime.commands()))
        with self.assertRaisesRegex(
            RuntimeError,
            "intent id already exists with different content",
        ):
            await self.system.runtime.submit_intent(Intent(
                intent_id=intent.intent_id,
                actor_id=intent.actor_id,
                resource_id=intent.resource_id,
                capability_id=intent.capability_id,
                arguments={"seconds": 20},
                requested_at=intent.requested_at,
                rationale=intent.rationale,
            ))

    async def test_operator_can_reject_pending_command_with_audit_context(self):
        pending = await self.system.request_sampling_interval(
            actor_id="agent.water-observer",
            station_id=STATION_ID,
            seconds=10,
            rationale="Increase observation frequency",
            correlation_id="flood-episode-rejected",
        )

        rejected = await self.system.runtime.reject(
            pending.command_id,
            rejector_id="operator-002",
            reason="现场正在检修，维持当前采样频率",
        )

        self.assertEqual(CommandState.REJECTED, rejected.state)
        self.assertEqual("operator-002", rejected.rejected_by)
        self.assertEqual(
            "现场正在检修，维持当前采样频率",
            rejected.rejection_reason,
        )
        self.assertEqual([], self.transport.published)
        event = self.system.runtime.events(
            event_type="domain.command.rejected",
        )[-1]
        self.assertEqual("operator-002", event.data["rejected_by"])
        self.assertEqual("flood-episode-rejected", event.correlation_id)
        with self.assertRaisesRegex(RuntimeError, "not pending approval"):
            await self.system.runtime.approve(
                rejected.command_id,
                approver_id="operator-001",
            )

    async def test_preexisting_state_cannot_confirm_a_new_command(self):
        topic = f"water/stations/{STATION_ID}/telemetry"
        await self.transport.inject(
            topic,
            telemetry(
                message_id="already-matching",
                observed_at="2026-08-26T09:00:00+08:00",
                sequence=1,
                metrics={"sampling_interval_seconds": {"value": 10, "unit": "s"}},
            ),
        )

        pending = await self.system.request_sampling_interval(
            actor_id="agent.water-observer",
            station_id=STATION_ID,
            seconds=10,
            rationale="Keep the high-frequency observation mode active",
        )
        acknowledged = await self.system.runtime.approve(
            pending.command_id,
            approver_id="operator-001",
        )

        self.assertEqual(CommandState.ACKNOWLEDGED, acknowledged.state)

        await self.transport.inject(
            topic,
            telemetry(
                message_id="confirmed-after-dispatch",
                observed_at="2026-08-26T09:01:00+08:00",
                sequence=2,
                metrics={"sampling_interval_seconds": {"value": 10, "unit": "s"}},
            ),
        )
        self.assertEqual(
            CommandState.CONFIRMED,
            self.system.runtime.command(pending.command_id).state,
        )

    async def test_driver_can_report_retained_observation_during_start(self):
        system = create_flood_domain_system(
            mqtt_transport=RetainedTelemetryTransport(),
            station_ids=(STATION_ID,),
        )
        await system.start()
        try:
            self.assertEqual(
                246.3,
                system.runtime.projection(RESOURCE_ID)["water_level_m"].value,
            )
        finally:
            await system.stop()

    async def test_direct_command_emits_dispatching_event_once(self):
        transport = InMemoryMqttTransport()
        system = create_flood_domain_system(
            mqtt_transport=transport,
            station_ids=(STATION_ID,),
            policy=AllowPolicy(),
        )
        await system.start()
        try:
            command = await system.request_sampling_interval(
                actor_id="agent.water-observer",
                station_id=STATION_ID,
                seconds=10,
                rationale="Increase observation frequency",
            )

            self.assertEqual(CommandState.ACKNOWLEDGED, command.state)
            self.assertEqual(
                1,
                len(system.runtime.events(
                    event_type="domain.command.dispatching"
                )),
            )
        finally:
            await system.stop()

    async def test_state_and_acknowledged_command_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "domain-state.sqlite"
            first_store = SqliteDomainStore(database)
            first_transport = InMemoryMqttTransport()
            first_system = create_flood_domain_system(
                mqtt_transport=first_transport,
                station_ids=(STATION_ID,),
                store=first_store,
            )
            await first_system.start()
            await first_transport.inject(
                f"water/stations/{STATION_ID}/telemetry",
                telemetry(
                    message_id="before-restart",
                    observed_at="2026-08-26T09:00:00+08:00",
                    sequence=1,
                    metrics={
                        "water_level_m": {"value": 246.8, "unit": "m"},
                        "sampling_interval_seconds": {"value": 60, "unit": "s"},
                    },
                ),
            )
            pending = await first_system.request_sampling_interval(
                actor_id="agent.water-observer",
                station_id=STATION_ID,
                seconds=10,
                rationale="Increase observation frequency",
                correlation_id="persistent-flood-episode",
            )
            acknowledged = await first_system.runtime.approve(
                pending.command_id,
                approver_id="operator-001",
            )
            self.assertEqual(CommandState.ACKNOWLEDGED, acknowledged.state)
            await first_system.stop()
            first_store.close()

            second_store = SqliteDomainStore(database)
            second_transport = InMemoryMqttTransport()
            second_system = create_flood_domain_system(
                mqtt_transport=second_transport,
                station_ids=(STATION_ID,),
                store=second_store,
            )
            await second_system.start()
            try:
                projection = second_system.runtime.projection(RESOURCE_ID)
                self.assertEqual(246.8, projection["water_level_m"].value)
                self.assertEqual(
                    CommandState.ACKNOWLEDGED,
                    second_system.runtime.command(pending.command_id).state,
                )

                await second_transport.inject(
                    f"water/stations/{STATION_ID}/telemetry",
                    telemetry(
                        message_id="after-restart",
                        observed_at="2026-08-26T09:01:00+08:00",
                        sequence=2,
                        metrics={
                            "sampling_interval_seconds": {"value": 10, "unit": "s"}
                        },
                    ),
                )

                confirmed = second_system.runtime.command(pending.command_id)
                self.assertEqual(CommandState.CONFIRMED, confirmed.state)
                self.assertEqual(
                    "persistent-flood-episode",
                    second_system.runtime.events(
                        event_type="domain.command.confirmed"
                    )[0].correlation_id,
                )
            finally:
                await second_system.stop()
                second_store.close()

    async def test_dispatch_interrupted_by_restart_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "domain-state.sqlite"
            store = SqliteDomainStore(database)
            now = utc_now()
            command = Command(
                command_id=new_id("command"),
                intent=Intent(
                    intent_id=new_id("intent"),
                    actor_id="agent.water-observer",
                    resource_id=RESOURCE_ID,
                    capability_id=SET_SAMPLING_INTERVAL,
                    arguments={"seconds": 10},
                    requested_at=now,
                    rationale="Increase observation frequency",
                ),
                driver_id=MQTT_DRIVER_ID,
                state=CommandState.DISPATCHING,
                created_at=now,
                updated_at=now,
                dispatched_at=now,
            )
            store.save_command(DOMAIN_ID, command)
            store.close()

            restored_store = SqliteDomainStore(database)
            transport = InMemoryMqttTransport()
            system = create_flood_domain_system(
                mqtt_transport=transport,
                station_ids=(STATION_ID,),
                store=restored_store,
            )
            await system.start()
            try:
                recovered = system.runtime.command(command.command_id)
                self.assertEqual(CommandState.OUTCOME_UNKNOWN, recovered.state)
                self.assertEqual([], transport.published)
                self.assertEqual(
                    1,
                    len(system.runtime.events(
                        event_type="domain.command.outcome_unknown"
                    )),
                )
            finally:
                await system.stop()
                restored_store.close()

    async def test_mqtt_topic_prefix_isolated_per_deployment(self):
        prefix = "agent-domain-os/test-run/water"
        transport = InMemoryMqttTransport()
        system = create_flood_domain_system(
            mqtt_transport=transport,
            station_ids=(STATION_ID,),
            mqtt_topic_prefix=prefix,
        )
        await system.start()
        try:
            await transport.inject(
                f"{prefix}/stations/{STATION_ID}/telemetry",
                telemetry(
                    message_id="namespaced-telemetry",
                    observed_at="2026-08-26T09:00:00+08:00",
                    sequence=1,
                    metrics={
                        "sampling_interval_seconds": {"value": 60, "unit": "s"}
                    },
                ),
            )
            pending = await system.request_sampling_interval(
                actor_id="agent.water-observer",
                station_id=STATION_ID,
                seconds=10,
                rationale="Increase observation frequency",
            )
            await system.runtime.approve(
                pending.command_id,
                approver_id="operator-001",
            )

            self.assertEqual(
                f"{prefix}/stations/{STATION_ID}/commands",
                transport.published[0].topic,
            )
        finally:
            await system.stop()


if __name__ == "__main__":
    unittest.main()
