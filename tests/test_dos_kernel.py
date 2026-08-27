"""Unit tests for the dos kernel — OS-semantic behaviours, not flood."""

from __future__ import annotations

import os
import tempfile
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
from dos.persistence import JsonlSink, load_journal, recover


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
        observed_at = raw.get("ts")
        if observed_at is not None:
            yield "/plant/valve-1/position", self.position, float(observed_at)
        else:
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

    def test_act_idempotent_reuse_of_inflight_txn(self):
        kernel = make_kernel()
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        first = kernel.act(cap.token, "/plant/valve-1", "close")
        second = kernel.act(cap.token, "/plant/valve-1", "close")  # retry while in flight
        self.assertEqual(first.txn_id, second.txn_id)
        self.assertTrue(second.reused)
        self.assertEqual(len(kernel.consistency.pending()), 1)

    def test_stale_evidence_never_commits(self):
        kernel = make_kernel()
        kernel.drivers["valve-1"].privileged_actions = frozenset()
        # the world already reports the target position
        kernel.interrupt("valve-1", {"position": "closed"})
        kernel.pump()
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        kernel.drivers["valve-1"].dispatched.clear()
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        kernel.pump()
        # namespace value equals the target, but the evidence predates the
        # dispatch — the txn must remain open until fresh telemetry arrives
        self.assertEqual(kernel.txn(result.txn_id).state, "dispatched")
        kernel.interrupt("valve-1", {"position": "closed"})
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "committed")

    def test_approval_timeout_fails_txn(self):
        now = {"t": 1000.0}
        kernel = Kernel(clock=lambda: now["t"])
        kernel.mount("/plant", ValveDriver())
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        result = kernel.act(cap.token, "/plant/valve-1", "close")  # close is privileged
        self.assertEqual(result.state, "awaiting_approval")
        now["t"] += 301.0  # past default_approval_timeout (300s)
        kernel.pump()
        self.assertEqual(kernel.txn(result.txn_id).state, "failed")
        self.assertIn("approval timed out", kernel.txn(result.txn_id).error)


class TestMounting(unittest.TestCase):
    def test_duplicate_prefix_rejected(self):
        kernel = make_kernel()
        with self.assertRaises(RuntimeError):
            kernel.mount("/plant", ValveDriver())

    def test_longest_prefix_wins(self):
        kernel = make_kernel()
        specific = ValveDriver()
        specific.device_id = "valve-1-specific"
        kernel.mount("/plant/valve-1", specific)
        cap = kernel.grant("/plant/valve-1", {"close"}, "test")
        result = kernel.act(cap.token, "/plant/valve-1", "close")
        self.assertIn(kernel.txn(result.txn_id).device_id, ("valve-1-specific",))
        self.assertEqual(kernel.txn(result.txn_id).device_id, "valve-1-specific")


class TestWatchIsolation(unittest.TestCase):
    def test_watcher_exception_does_not_break_commit(self):
        kernel = Kernel()

        def boom(_snap):
            raise RuntimeError("watcher bug")

        kernel.watch("/a", boom)
        kernel.namespace.write("/a/value", 1, source_seq=1)
        self.assertEqual(kernel.read("/a/value").value, 1)
        self.assertEqual(len(kernel.namespace.watch_errors), 1)


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

    def test_budget_overrun_marks_failure_and_kernel_survives(self):
        import time as _time

        kernel = make_kernel()
        side_effects: list[str] = []

        def hog(ctx):
            _time.sleep(0.3)
            side_effects.append("done")

        proc = kernel.spawn(ProcessSpec(name="hog", watches=("/plant/valve-1/position",), handler=hog, budget_seconds=0.05, restart_limit=1))
        kernel.interrupt("valve-1", {"position": "open"})
        stats = kernel.pump()
        self.assertNotIn("hog", stats["ran"])
        self.assertEqual(proc.state.value, "backoff")
        self.assertIn("budget", proc.last_error)
        # kernel keeps pumping regardless
        kernel.interrupt("valve-1", {"position": "closed"})
        kernel.pump()
        self.assertEqual(kernel.read("/plant/valve-1/position").value, "closed")


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


class PathDriver(Driver):
    """Generic driver for recovery scenarios: a value path per mount."""

    def __init__(self, base: str, obey: bool = True, privileged=()):
        self.base = base.rstrip("/")
        self.device_id = "dev" + self.base.replace("/", "-")
        self.obey = obey
        self.privileged_actions = frozenset(privileged)
        self.dispatched: list[PendingTxn] = []

    def normalize(self, raw):
        yield f"{self.base}/value", raw

    def dispatch(self, txn):
        self.dispatched.append(txn)

    def verify(self, txn, read):
        snap = read(f"{self.base}/value")
        if snap is None:
            return "pending"
        return "committed" if snap.value == txn.args.get("want") else "pending"


class TestRecovery(unittest.TestCase):
    def _scenario(self, journal, now):
        kernel = Kernel(journal=journal, clock=lambda: now["t"])
        plant = PathDriver("/plant", obey=True, privileged=("reset",))
        deaf = PathDriver("/deaf", obey=False)
        kernel.mount("/plant", plant)
        kernel.mount("/deaf", deaf)
        cap = kernel.grant("/plant", {"set", "reset"}, "boot")
        deaf_cap = kernel.grant("/deaf", {"set"}, "boot")

        kernel.interrupt(plant.device_id, "ready")
        kernel.pump()
        committed = kernel.act(cap.token, "/plant", "set", {"want": "ready"})
        kernel.interrupt(plant.device_id, "ready")  # fresh evidence
        kernel.pump()
        parked = kernel.act(cap.token, "/plant", "reset", {"want": "zero"})  # privileged → awaiting
        lost = kernel.act(deaf_cap.token, "/deaf/value", "set", {"want": "x"})  # device never obeys
        now["t"] += 60.0  # past deaf driver's 10s timeout
        kernel.pump()  # → unknown + frozen

        return kernel, cap, committed, parked, lost

    def test_recover_rebuilds_world_caps_pending_and_freezes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "journal.jsonl")
            now = {"t": 1000.0}
            k1, cap, committed, parked, lost = self._scenario(
                Journal(clock=lambda: now["t"], sink=JsonlSink(path)), now
            )
            # preconditions of the scenario itself
            self.assertEqual(k1.txn(committed.txn_id).state, "committed")
            self.assertEqual(k1.txn(parked.txn_id).state, "awaiting_approval")
            self.assertEqual(k1.txn(lost.txn_id).state, "unknown")
            self.assertIsNotNone(k1.consistency.is_frozen("/deaf/value"))

            # reboot: same journal file, fresh kernel
            k2 = Kernel(journal=load_journal(path), clock=lambda: 5000.0)
            k2.mount("/plant", PathDriver("/plant", obey=True, privileged=("reset",)))
            k2.mount("/deaf", PathDriver("/deaf", obey=False))
            stats = recover(k2)

            self.assertEqual(stats["capabilities"], 2)
            self.assertEqual(k2.read("/plant/value").value, "ready")  # namespace rebuilt
            self.assertEqual(k2.read("/plant/value").source_seq, k1.read("/plant/value").source_seq)
            # capabilities survive with the same tokens
            k2.caps.check(cap.token, "/plant", "set")
            with self.assertRaises(CapabilityError):
                k2.caps.check(cap.token, "/deaf", "set")
            # transaction states survive
            self.assertEqual(k2.txn(committed.txn_id).state, "committed")
            self.assertEqual(k2.txn(parked.txn_id).state, "awaiting_approval")
            self.assertGreater(k2.txn(parked.txn_id).approval_deadline, 5000.0)  # fresh grace window
            self.assertEqual(k2.txn(lost.txn_id).state, "unknown")
            self.assertIsNotNone(k2.consistency.is_frozen("/deaf/value"))  # freeze survives reboot
            fresh_deaf_cap = k2.grant("/deaf", {"set"}, "reboot")
            with self.assertRaises(FrozenPathError):
                k2.act(fresh_deaf_cap.token, "/deaf/value", "set", {"want": "y"})
            # and the kernel keeps working after recovery
            r = k2.act(cap.token, "/plant", "set", {"want": "ready2"})
            self.assertEqual(r.state, "dispatched")


if __name__ == "__main__":
    unittest.main()
