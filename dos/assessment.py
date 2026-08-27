"""Assessment filing — the device that lets standing agents file their
judgments into the world.

A standing agent's output ("20 年一遇量级，桥是高危") must not write the
namespace directly; it enters the world the same way every model output
does: as a transaction on a device.  ``AssessmentDevice`` is that device,
generic and domain-free:

    act("file_assessment", {kind, title, content, refs})
      → the device commits its own job telemetry
      → assessments/{id} (full record, size-capped)
      → assessments/latest and assessments/by-kind/{kind}/latest pointers

Filings are lightweight transactions: no worker thread, no long job —
dispatch queues the evidence immediately and fsck commits it on the next
pump.  Assessments are records, not actions on reality, so filing is
unprivileged (the approval gate belongs to commands that change the
world).  ``author`` travels in args for v1 — trust boundary: a gateway
could inject the session principal instead.

Also here: ``spawn_observer_watchdog`` — the companion discipline.  A
deterministic process (timer-woken) watches standing agents' gateway
sessions and files an ``observer-offline`` assessment when one goes
quiet.  Processes watch agents; the kernel watches processes.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from dos import Driver, Kernel, ProcessSpec
from dos.devices import PendingTxn

FILE_ASSESSMENT = "file_assessment"
MAX_CONTENT_CHARS = 32_000
ASSESSMENT_DEADLINE_S = 30.0


class AssessmentDevice(Driver):
    device_id = "assessments"
    default_txn_timeout = ASSESSMENT_DEADLINE_S

    def __init__(self, base: str):
        self.base = "/" + base.strip("/")
        self.last_error: Optional[str] = None

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        if action != FILE_ASSESSMENT:
            return f"unsupported action: {action}"
        title = args.get("title")
        if not isinstance(title, str) or not title.strip():
            return "args.title is required"
        content = args.get("content")
        if not isinstance(content, (str, dict)) or not content:
            return "args.content must be a non-empty string or object"
        if isinstance(content, str) and len(content) > MAX_CONTENT_CHARS:
            return f"args.content exceeds {MAX_CONTENT_CHARS} chars — put large documents on disk and reference them"
        if args.get("kind") is not None and not isinstance(args["kind"], str):
            return "args.kind must be a string"
        return None

    # dispatch is synchronous: the evidence is queued immediately and
    # committed by fsck on the next pump
    def dispatch(self, txn: PendingTxn) -> None:
        assessment_id = f"asmt_{txn.dispatched_seq:06d}"
        self.kernel.interrupt(
            self.device_id,
            {
                "kind": "job_done",
                "txn_id": txn.txn_id,
                "assessment_id": assessment_id,
                "args": dict(txn.args),
            },
        )

    def normalize(self, raw: object):
        event = raw
        if event.get("kind") != "job_done":
            self.last_error = f"unknown frame: {event.get('kind')}"
            return
        assessment_id = event["assessment_id"]
        args = event["args"]
        record = {
            "id": assessment_id,
            "txn_id": event["txn_id"],
            "kind": args.get("kind") or "note",
            "title": args["title"],
            "content": args["content"],
            "refs": args.get("refs") or {},
            "author": args.get("author") or "unknown",
            "filed_at": time.time(),
        }
        now = time.time()
        yield self.path("last_job"), {"txn_id": event["txn_id"], "status": "done", "assessment_id": assessment_id}, now
        yield self.path(assessment_id), record, now
        yield self.path("latest"), {"id": assessment_id, "kind": record["kind"]}, now
        yield self.path(f"by-kind/{record['kind']}/latest"), {"id": assessment_id, "kind": record["kind"]}, now

    def verify(self, txn: PendingTxn, read) -> str:
        snap = read(self.path("last_job"))
        if snap is None:
            return "pending"
        evidence = snap.value
        if evidence.get("txn_id") != txn.txn_id:
            return "pending"
        return "committed" if evidence.get("status") == "done" else "pending"

    def path(self, suffix: str) -> str:
        return f"{self.base}/{suffix.lstrip('/')}"


def mount_assessments(kernel: Kernel, base: str) -> AssessmentDevice:
    device = AssessmentDevice(base)
    kernel.mount(device.base, device)
    return device


def spawn_observer_watchdog(
    kernel: Kernel,
    cap_token: str,
    gateway,
    expected: dict[str, float],
    *,
    check_every: float = 30.0,
    alert_cooldown: float = 300.0,
    assessments_base: str = "/assessments",
    sink: Optional[list] = None,
) -> None:
    """Watch the watchers: file an observer-offline assessment when a
    standing agent's gateway session goes quiet.

    ``expected`` maps principal -> max idle seconds.  The watchdog calls
    ``gateway.reap_idle()`` each cycle (enforcing the gateway TTL, if
    configured) and files alerts with a cooldown.  State kept in the
    closure is just alert bookkeeping — losing it re-alerts, which is the
    safe direction."""
    events = sink if sink is not None else []
    last_alert: dict[str, float] = {}

    def handler(ctx):
        gateway.reap_idle()
        activity = {row["principal"]: row for row in gateway.activity()}
        now = time.time()
        for principal, max_idle in expected.items():
            row = activity.get(principal)
            if row is not None and row["idle_seconds"] <= max_idle:
                continue
            if now - last_alert.get(principal, 0.0) < alert_cooldown:
                continue
            result = ctx.act(
                cap_token,
                f"{assessments_base}/latest",
                FILE_ASSESSMENT,
                {
                    "kind": "observer-offline",
                    "title": f"常驻 agent 失联: {principal}",
                    "content": {
                        "principal": principal,
                        "idle_seconds": row["idle_seconds"] if row else None,
                        "max_idle_seconds": max_idle,
                    },
                    "refs": {"principal": principal},
                    "author": "observer-watchdog",
                },
            )
            last_alert[principal] = now
            events.append(f"offline:{principal}->{result.state}")

    kernel.spawn(
        ProcessSpec(
            name="observer-watchdog",
            watches=(),
            handler=handler,
            every_seconds=check_every,
            priority=3,
            budget_seconds=5.0,
            description="看守常驻 agent：会话超时无活动 → 立案告警",
        )
    )
