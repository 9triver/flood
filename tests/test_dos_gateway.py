"""Gateway unit tests — sessions, read scoping, act bridging, watch long-poll."""

from __future__ import annotations

import threading
import time
import unittest

from dos import Journal, Kernel
from dos.gateway import DosGateway, GatewayError, ReadScopeError
from tests.test_dos_kernel import ValveDriver

INTERVAL = "/plant/valve-1/position"


def make_gateway(**kwargs) -> DosGateway:
    kernel = Kernel(clock=lambda: time.time())
    kernel.mount("/plant", ValveDriver())
    kernel.drivers["valve-1"].privileged_actions = frozenset()
    return DosGateway(kernel, **kwargs)


class TestSessions(unittest.TestCase):
    def test_open_and_close(self):
        gw = make_gateway()
        session = gw.open_session("agent-a", read_scopes=("/plant",))
        self.assertEqual(gw.session(session.session_id).principal, "agent-a")
        gw.close_session(session.session_id)
        with self.assertRaises(GatewayError):
            gw.session(session.session_id)

    def test_act_grant_refused_when_disabled(self):
        gw = make_gateway(allow_act_grant=False)
        with self.assertRaises(GatewayError):
            gw.open_session("agent-a", act_prefix="/plant", act_actions=["close"])

    def test_no_act_session_cannot_act(self):
        gw = make_gateway()
        session = gw.open_session("agent-a", read_scopes=("/plant",))
        with self.assertRaises(ReadScopeError):
            gw.act(session.session_id, INTERVAL, "close")


class TestReadScoping(unittest.TestCase):
    def test_scope_enforced(self):
        gw = make_gateway()
        kernel = gw.kernel
        kernel.namespace.write("/plant/x", 1, 1)
        kernel.namespace.write("/other/y", 2, 1)
        scoped = gw.open_session("scoped", read_scopes=("/plant",))
        self.assertEqual(gw.read(scoped.session_id, "/plant/x")["value"], 1)
        with self.assertRaises(ReadScopeError):
            gw.read(scoped.session_id, "/other/y")
        listed = gw.list_paths(scoped.session_id, "/")
        self.assertIn("/plant/x", listed)
        self.assertNotIn("/other/y", listed)

    def test_root_scope_reads_everything(self):
        gw = make_gateway()
        kernel = gw.kernel
        kernel.namespace.write("/other/y", 2, 1)
        session = gw.open_session("wide")
        self.assertEqual(gw.read(session.session_id, "/other/y")["value"], 2)


class TestActBridge(unittest.TestCase):
    def test_act_uses_session_token(self):
        gw = make_gateway()
        session = gw.open_session("op", read_scopes=("/plant",), act_prefix="/plant/valve-1", act_actions=["close"])
        result = gw.act(session.session_id, INTERVAL, "close")
        self.assertEqual(result["state"], "dispatched")
        gw.kernel.interrupt("valve-1", {"position": "closed"})
        gw.kernel.pump()
        self.assertEqual(gw.txn_status(result["txn_id"])["state"], "committed")

    def test_close_session_revokes_capability(self):
        gw = make_gateway()
        session = gw.open_session("op", read_scopes=("/plant",), act_prefix="/plant/valve-1", act_actions=["close"])
        sid = session.session_id
        gw.close_session(sid)
        with self.assertRaises(GatewayError):
            gw.act(sid, INTERVAL, "close")


class TestWatchLongPoll(unittest.TestCase):
    def test_wait_for_change_returns_on_commit(self):
        gw = make_gateway()
        kernel = gw.kernel
        kernel.namespace.write("/plant/x", 1, 1)
        session = gw.open_session("watcher", read_scopes=("/plant",))
        snap_generation = kernel.read("/plant/x").generation

        def commit_later() -> None:
            time.sleep(0.1)
            kernel.namespace.write("/plant/x", 2, 2)

        threading.Thread(target=commit_later, daemon=True).start()
        started = time.time()
        result = gw.wait_for_change(session.session_id, ["/plant/x"], {"/plant/x": snap_generation}, timeout=5)
        self.assertLess(time.time() - started, 2)
        self.assertIn("/plant/x", result["changed"])

    def test_wait_for_change_times_out_quietly(self):
        gw = make_gateway()
        gw.kernel.namespace.write("/plant/x", 1, 1)
        session = gw.open_session("watcher", read_scopes=("/plant",))
        started = time.time()
        result = gw.wait_for_change(session.session_id, ["/plant/x"], {"/plant/x": 999}, timeout=0.3)
        self.assertGreaterEqual(time.time() - started, 0.25)
        self.assertEqual(result["changed"], {})

    def test_empty_paths_is_a_pure_timed_wait(self):
        """No watched paths -> wake exactly at the timeout (an observer
        agent's periodic tick)."""
        gw = make_gateway()
        session = gw.open_session("observer")
        started = time.time()
        result = gw.wait_for_change(session.session_id, [], {}, timeout=0.2)
        elapsed = time.time() - started
        self.assertGreaterEqual(elapsed, 0.19)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(result, {"changed": {}, "generations": {}})

    def test_watch_scope_enforced(self):
        gw = make_gateway()
        session = gw.open_session("scoped", read_scopes=("/plant",))
        with self.assertRaises(ReadScopeError):
            gw.wait_for_change(session.session_id, ["/other/y"], {}, timeout=0.1)


if __name__ == "__main__":
    unittest.main()
