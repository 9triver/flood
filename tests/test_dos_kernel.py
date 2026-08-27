"""Unit tests for the dos kernel — OS-semantic behaviours, not flood."""

from __future__ import annotations

import unittest
from typing import Optional

from dos import (
    CapabilityError,
    Driver,
    FrozenPathError,
    Journal,
    Kernel,
    NotFound,
    ProcessSpec,
    Snapshot,
)
from dos.devices import PendingTxn


class ValveDriver(Driver):
    """Minimal device: a valve with open/close commands, telemetry-confirmed."""

    privileged_actions = frozenset({"close"})

    def __init__(self):
        self.device_id = "valve-1"
        self.dispatched: list[PendingTxn] = []
        self.position = "open"
        self.confirm_next = True  # whether the world will actually obey

    def normalize(self, raw):
        self.position = raw["position"]
        yield "/plant/valve-1/position", self.position

    def dispatch(self, txn):
        self.dispatched.append(txn)
        if self.confirm_next:
            self.position = "closed" if txn.action == "close" else "open"

    def verify(self, txn, read) -> str:
        snap = read("/plant/valve-1/position")
        if snap is None:
            return "pending"
        want = "closed" if txn.action == "close" else "open"
        return "committed" if snap.value == want else "pending"


class DeafDriver(ValveDriver):
    """The device never obeys nor reports back — outcome becomes unknown."""

    def dispatch(self, txn):
        self.dispatched.append(txn)  # silently drops the command

    def verify(self, txn, read) -> str:
        return "pending"


def make_kernel(driver=None):
    kernel = Kernel(clock=lambda: __import__("itertools").count(1).__next__())
    kernel.mount("/plant", driver or ValveDriver())
    return kernel


class TestJournal(unittest.TestCase):
    def test_append_only_monotonic(self):
        j = Journal(clock=lambda: 0.0)
        r1 = j.append("observation", {"a": 1})
        r2 = j.append("note", {"b": 2})
        self.assertEqual((r1.seq, r2.seq), (1, 2))
        self.assertEqual([r.kind for r in j.replay()], ["observation", "note"])
        self.assertEqual(j.tail(1)[0].seq, 2)


class TestNamespace(unittest.TestCase):
    def test_generation_advances_only_on_change(self):
        ns = Kernel().namespace
        self.assertTrue(ns.write("/a/b", 1, source_seq=1))
        snap = ns.read("/a/b")
        self.assertEqual((snap.value, snap.generation), (1, 1))
        ns.write("/a/b", 1, source_seq=2)  # same value
        self.assertEqual(ns.read("/a/b").generation, 1)
        ns.write("/a/b", 2, source_seq=3)
        self.assertEqual(ns.read("/a/b").generation, 2)

    def test_watch_subtree_and_cancel(self):
        ns = Kernel().namespace
        seen: list[str] = []
        cancel = ns.watch("/a", lambda s: seen.append(s.path))
        ns.write("/a/x", 1, 1)
        ns.write("/a/deep/y", 2, 1)
        ns.write("/other/z", 3, 1)
        cancel()
        ns.write("/a/x", 4, 1)
        self.assertEqual(seen, ["/a/x", "/a/deep/y"])

    def test_derived_view_invalidated_by_dependency(self):
        ns = Kernel().namespace
        ns.write("/t", 1, 1)
        ns.derive("/view", ("/t",), lambda ns_: ns_.read("/t").value * 10)
        self.assertEqual(ns.read("/view").value, 10)
        self.assertTrue(ns.read("/view").derived)
        ns.write("/t", 2, 2)
        self.assertEqual(ns.read("/view").value, 20)  # recomputed lazily
        self.assertEqual(ns.read("/view").generation, 2)

    def test_read_missing_raises(self):
        with self.assertRaises(NotFound):
            Kernel().namespace.read("/nope")


class TestCapabilities(unittest.TestCase):
    def test_scoping(self):
        kernel = make_kernel()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        kernel.caps.check(cap.token, "/plant/valve-1/position", "close")
        with self.assertRaises(CapabilityError):
            kernel.caps.check(cap.token, "/plant/valve-1", "open")  # wrong action
        with self.assertRaises(CapabilityError):
            kernel.caps.check(cap.token, "/other", "close")  # out of subtree

    def test_unknown_token(self):
        kernel = make_kernel()
        with self.assertRaises(CapabilityError):
            kernel.caps.check("cap_bogus", "/plant/valve-1", "close")


class TestActSyscall(unittest.TestCase):
    def test_happy_path_committed_by_telemetry(self):
        kernel = make_kernel()
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        self.assertEqual(result.state, "dispatched")
        kernel.pump()  # device obays synchronously, but confirmation needs uplink
        self.assertEqual(kernel.txn(result.txn_id).state, "dispatched")
        kernel.interrupt("valve-1", {"position": "closed"})
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "committed")

    def test_privileged_requires_approval_then_dispatch(self):
        kernel = make_kernel()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        # close is privileged on ValveDriver; use an unprivileged mount variant
        kernel.drivers["valve-1"].privileged_actions = frozenset({"close"})
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        self.assertEqual(result.state, "awaiting_approval")
        result = kernel.approve(result.txn_id, approved_by="op", decision=True)
        self.assertEqual(result.state, "dispatched")

    def test_approval_rejection_fails_txn(self):
        kernel = make_kernel()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        result = kernel.approve(result.txn_id, approved_by="op", decision=False, reason="no")
        self.assertEqual(result.state, "failed")
        self.assertEqual(kernel.txn(result.txn_id).error, "rejected by approver")

    def test_capability_denial_blocks_before_journal(self):
        kernel = make_kernel()
        with self.assertRaises(CapabilityError):
            kernel.act("cap_bogus", "/plant/valve-1", "close")
        self.assertEqual([r for r in kernel.journal.replay() if r.kind == "txn"], [])

    def test_precondition_cas(self):
        kernel = make_kernel()
        kernel.interrupt("valve-1", {"position": "open"})
        kernel.pump()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        # world moved on: precondition stale -> refused
        from dos import PreconditionError

        with self.assertRaises(PreconditionError):
            kernel.act(cap.token, "/plant/valve-1", "close", expect={"/plant/valve-1/position": "closed"})

    def test_timeout_marks_unknown_and_freezes(self):
        now = {"t": 1000.0}
        kernel = Kernel(clock=lambda: now["t"])
        kernel.mount("/plant", DeafDriver())
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        kernel.interrupt("valve-1", {"position": "open"})  # uplink: no change
        kernel.pump()
        now["t"] += 60.0  # well past the 10s txn deadline
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "unknown")
        with self.assertRaises(FrozenPathError):
            kernel.act(cap.token, "/plant/valve-1", "close")
        kernel.consistency.thaw("/plant/valve-1", "manual inspection done")
        self.assertIsNone(kernel.consistency.is_frozen("/plant/valve-1"))


class TestProcesses(unittest.TestCase):
    def test_wakeup_and_run(self):
        kernel = make_kernel()
        ran: list[object] = []
        kernel.spawn(ProcessSpec(name="watcher", watches=("/plant/valve-1/position",), handler=lambda ctx: ran.append(ctx.read("/plant/valve-1/position").value)))
        kernel.interrupt("valve-1", {"position": "open"})
        stats = kernel.pump()
        self.assertIn("watcher", stats["ran"])
        self.assertEqual(ran, ["open"])

    def test_handler_crash_is_supervised(self):
        kernel = make_kernel()
        calls = {"n": 0}

        def bad_handler(ctx):
            calls["n"] += 1
            raise RuntimeError("model went off the rails")

        proc = kernel.spawn(ProcessSpec(name="bad", watches=("/plant/valve-1/position",), handler=bad_handler, restart_limit=2))
        for _ in range(5):
            kernel.interrupt("valve-1", {"position": "open"})
            kernel.pump()
        self.assertLess(calls["n"], 5)  # not every wake re-ran: backoff/failed
        self.assertEqual(proc.state.value, "failed")
        # kernel namespace is still healthy
        self.assertEqual(kernel.read("/plant/valve-1/position").value, "open")

    def test_priority_ordering(self):
        kernel = make_kernel()
        order: list[str] = []
        kernel.spawn(ProcessSpec(name="low", watches=("/plant/valve-1/position",), handler=lambda ctx: order.append("low"), priority=0))
        kernel.spawn(ProcessSpec(name="high", watches=("/plant/valve-1/position",), handler=lambda ctx: order.append("high"), priority=9))
        kernel.interrupt("valve-1", {"position": "open"})
        kernel.pump()
        self.assertEqual(order, ["high", "low"])


class TestKernelBasics(unittest.TestCase):
    def test_no_device_mounted(self):
        kernel = Kernel()
        cap = kernel.grant("/nowhere", {"poke"}, "test")
        with self.assertRaises(KeyError):
            kernel.act(cap.token, "/nowhere/x", "poke")

    def test_pump_drains_interrupts_in_order(self):
        kernel = make_kernel()
        kernel.interrupt("valve-1", {"position": "open"})
        kernel.interrupt("valve-1", {"position": "closed"})
        stats = kernel.pump()
        self.assertEqual(stats["interrupts"], 2)
        self.assertEqual(kernel.read("/plant/valve-1/position").value, "closed")


if __name__ == "__main__":
    unittest.main()
