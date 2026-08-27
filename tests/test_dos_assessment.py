"""Assessment filing, session TTL, and the observer watchdog."""

from __future__ import annotations

import time
import unittest

from dos import DosGateway, InvalidActionError, Kernel, mount_assessments, spawn_observer_watchdog
from dos.gateway import ReadScopeError
from tests.test_dos_kernel import ValveDriver

BASE = "/ops/assessments"
LATEST = f"{BASE}/latest"


def make_kernel() -> Kernel:
    kernel = Kernel(clock=lambda: time.time())
    kernel.mount("/plant", ValveDriver())
    kernel.drivers["valve-1"].privileged_actions = frozenset()
    mount_assessments(kernel, BASE)
    return kernel


class TestAssessmentDevice(unittest.TestCase):
    def test_file_and_commit(self):
        kernel = make_kernel()
        cap = kernel.grant(BASE, {"file_assessment"}, "test")
        result = kernel.act(cap.token, LATEST, "file_assessment", {"kind": "situation", "title": "T0 态势", "content": "20 年一遇", "refs": {"forecast_id": "fcst_1"}, "author": "duty-agent"})
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "committed")
        latest = kernel.read(LATEST).value
        self.assertTrue(latest["id"].startswith("asmt_"))
        record = kernel.read(f"{BASE}/{latest['id']}").value
        self.assertEqual(record["title"], "T0 态势")
        self.assertEqual(record["refs"]["forecast_id"], "fcst_1")
        self.assertEqual(kernel.read(f"{BASE}/by-kind/situation/latest").value["id"], latest["id"])

    def test_validate_rejects_garbage(self):
        kernel = make_kernel()
        cap = kernel.grant(BASE, {"file_assessment", "poke"}, "test")
        with self.assertRaises(InvalidActionError):
            kernel.act(cap.token, LATEST, "file_assessment", {"title": ""})
        with self.assertRaises(InvalidActionError):
            kernel.act(cap.token, LATEST, "file_assessment", {"title": "x", "content": "y" * 40_000})
        with self.assertRaises(InvalidActionError):
            kernel.act(cap.token, LATEST, "poke", {})

    def test_gateway_scoped_filing(self):
        kernel = make_kernel()
        kernel.namespace.write("/plant/x", 1, 1)
        gw = DosGateway(kernel)
        agent = gw.open_session("duty-agent", read_scopes=("/ops",), act_prefix=BASE, act_actions=["file_assessment"])
        result = gw.act(agent.session_id, LATEST, "file_assessment", {"title": "研判", "content": {"level": "high"}})
        kernel.pump()
        self.assertEqual(kernel.txn(result["txn_id"]).state, "committed")


class TestSessionTTL(unittest.TestCase):
    def test_reap_idle_closes_and_revokes(self):
        kernel = make_kernel()
        gw = DosGateway(kernel, idle_ttl=0.2)
        session = gw.open_session("sitting-agent", act_prefix=BASE, act_actions=["file_assessment"])
        self.assertEqual(gw.reap_idle(), [])  # fresh session survives
        time.sleep(0.3)
        closed = gw.reap_idle()
        self.assertEqual(closed, [session.session_id])
        with self.assertRaises(Exception):
            gw.act(session.session_id, LATEST, "file_assessment", {"title": "x", "content": "y"})

    def test_activity_touches_on_use(self):
        kernel = make_kernel()
        gw = DosGateway(kernel, idle_ttl=0.2)
        session = gw.open_session("busy-agent", read_scopes=("/",))
        time.sleep(0.15)
        gw.read(session.session_id, "/plant/x") if kernel.namespace.exists("/plant/x") else gw.list_paths(session.session_id)
        rows = {r["principal"]: r for r in gw.activity()}
        self.assertLess(rows["busy-agent"]["idle_seconds"], 0.15)


class TestObserverWatchdog(unittest.TestCase):
    def test_files_alert_when_observer_quiet(self):
        kernel = make_kernel()
        kernel.namespace.write("/plant/x", 1, 1)
        gw = DosGateway(kernel)
        gw.open_session("duty-agent", read_scopes=("/",))
        cap = kernel.grant(BASE, {"file_assessment"}, "watchdog-test")
        events: list[str] = []
        spawn_observer_watchdog(
            kernel, cap.token, gw,
            expected={"duty-agent": 0.05},
            check_every=0.1, alert_cooldown=10.0,
            assessments_base=BASE, sink=events,
        )
        deadline = time.time() + 5
        while not events and time.time() < deadline:
            kernel.pump()
            time.sleep(0.02)
        self.assertTrue(events, "watchdog should file an offline alert")
        deadline = time.time() + 5
        while not kernel.namespace.exists(f"{BASE}/by-kind/observer-offline/latest") and time.time() < deadline:
            kernel.pump()  # the filing's evidence interrupt lands on the next pump
            time.sleep(0.02)
        alert = kernel.read(f"{BASE}/by-kind/observer-offline/latest").value
        record = kernel.read(f"{BASE}/{alert['id']}").value
        self.assertEqual(record["refs"]["principal"], "duty-agent")
        # cooldown: no repeat within the window
        for _ in range(5):
            kernel.pump()
            time.sleep(0.02)
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
