from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from domain_os import DomainControlService, InMemoryMqttTransport, SqliteDomainStore
from domain_os import DomainQueryService
from domains.flood.domain_system import (
    SET_SAMPLING_INTERVAL,
    create_flood_domain_system,
    station_resource_id,
)
from server.domain_runtime_host import DomainRuntimeHost, DomainRuntimeHostError


class ThreadOwnedSystem:
    def __init__(self) -> None:
        self.owner_thread_id = threading.get_ident()
        self.connection = sqlite3.connect(":memory:")
        self.runtime = SimpleNamespace(domain_id="test.host")
        self.started_thread_id = None
        self.stopped_thread_id = None
        self.closed_thread_id = None

    async def start(self) -> None:
        self.started_thread_id = threading.get_ident()
        self.connection.execute("CREATE TABLE values_for_test (value INTEGER)")

    async def stop(self) -> None:
        self.stopped_thread_id = threading.get_ident()

    async def insert(self, value: int) -> tuple[int, int]:
        self.connection.execute(
            "INSERT INTO values_for_test (value) VALUES (?)",
            (value,),
        )
        self.connection.commit()
        count = self.connection.execute(
            "SELECT COUNT(*) FROM values_for_test",
        ).fetchone()[0]
        return threading.get_ident(), count

    def close(self) -> None:
        self.closed_thread_id = threading.get_ident()
        self.connection.close()


class DomainRuntimeHostTest(unittest.TestCase):
    def test_lifecycle_database_and_calls_share_one_thread(self):
        created = []

        def factory():
            system = ThreadOwnedSystem()
            created.append(system)
            return system, system.close

        host = DomainRuntimeHost(factory)
        host.start()
        system = created[0]
        try:
            call_thread_id, count = host.call(lambda: system.insert(7))

            self.assertEqual(1, count)
            self.assertEqual(host.thread_id, system.owner_thread_id)
            self.assertEqual(host.thread_id, system.started_thread_id)
            self.assertEqual(host.thread_id, call_thread_id)
            self.assertEqual("test.host", host.runtime.domain_id)
        finally:
            host.stop()

        self.assertEqual(system.owner_thread_id, system.stopped_thread_id)
        self.assertEqual(system.owner_thread_id, system.closed_thread_id)
        with self.assertRaisesRegex(DomainRuntimeHostError, "not running"):
            host.call(lambda: system.insert(8))

    def test_start_failure_is_reported_and_factory_resource_is_closed(self):
        closed = []

        class FailingSystem:
            runtime = SimpleNamespace(domain_id="test.failure")

            async def start(self):
                raise RuntimeError("driver unavailable")

            async def stop(self):
                raise AssertionError("stop must not run after failed start")

        host = DomainRuntimeHost(
            lambda: (FailingSystem(), lambda: closed.append(threading.get_ident())),
        )

        with self.assertRaisesRegex(
            DomainRuntimeHostError,
            "driver unavailable",
        ):
            host.start()

        self.assertEqual(1, len(closed))

    def test_real_domain_control_and_sqlite_share_host_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "hosted-domain.sqlite"

            def factory():
                store = SqliteDomainStore(database)
                system = create_flood_domain_system(
                    mqtt_transport=InMemoryMqttTransport(),
                    station_ids=("808J1510",),
                    store=store,
                )
                return system, store.close

            host = DomainRuntimeHost(factory)
            host.start()
            control = DomainControlService(host.runtime)
            queries = DomainQueryService(host.read_model)
            try:
                pending = host.call(lambda: control.submit_intent({
                    "intent_id": "intent-hosted-control",
                    "actor_id": "agent.host-test",
                    "resource_id": station_resource_id("808J1510"),
                    "capability_id": SET_SAMPLING_INTERVAL,
                    "arguments": {"seconds": 10},
                    "rationale": "Verify hosted control",
                }))
                rejected = host.call(lambda: control.reject(
                    pending["command_id"],
                    {
                        "rejector_id": "operator-host-test",
                        "reason": "Host lifecycle verification",
                    },
                ))
                self.assertEqual("rejected", rejected["state"])
                queried = queries.command(rejected["command_id"])
                self.assertEqual("rejected", queried["state"])
                initial = queries.events()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    waiting = pool.submit(
                        queries.wait_for_events,
                        after=initial["next_cursor"],
                        event_type="test.hosted-event",
                        timeout=2,
                    )
                    host.call(lambda: host.runtime.publish_event(
                        "test.hosted-event",
                        station_resource_id("808J1510"),
                        {"ok": True},
                    ))
                    received = waiting.result(timeout=2)
                self.assertEqual(1, received["count"])
                self.assertTrue(received["items"][0]["event"]["data"]["ok"])
            finally:
                queries.close()
                host.stop()

            store = SqliteDomainStore(database)
            try:
                persisted = store.load_commands("water.flood")
                self.assertEqual(1, len(persisted))
                self.assertEqual("rejected", persisted[0].state.value)
                self.assertEqual("operator-host-test", persisted[0].rejected_by)
                self.assertEqual(
                    "Host lifecycle verification",
                    persisted[0].rejection_reason,
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
