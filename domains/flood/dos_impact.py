"""Inundation impact assessment on the dos kernel — the second instance of
the compute-device pattern.

The ImpactDevice wraps the deterministic impact analysis as a device:
``act("analyze_impact", {forecast_id, targets})`` opens a transaction, a
worker thread runs the analysis against the forecast's artifacts, and the
transaction commits on fresh job evidence.  Two consumers share it:

- ``spawn_impact_auto`` — a stateless process watching forecasts/latest,
  running the standard target sweep whenever a new forecast lands;
- agents — via the MCP gateway with an act capability on
  ``/hydro/shanhu/impacts`` (ad-hoc targets).

Namespace layout (big geometries stay on disk; namespace holds summaries):

    /hydro/shanhu/impacts/pending     in-flight job | {} idle
    /hydro/shanhu/impacts/last_job    fsck evidence
    /hydro/shanhu/impacts/{id}        {forecast_id, targets, summary, artifacts}
    /hydro/shanhu/impacts/latest      pointer
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from dos import Driver, Kernel, ProcessSpec
from dos.devices import PendingTxn

from .dos_forecast import FORECAST_MOUNT, LATEST_PATH as FORECAST_LATEST

IMPACT_MOUNT = "/hydro/shanhu/impacts"
IMPACT_PENDING = f"{IMPACT_MOUNT}/pending"
IMPACT_LAST_JOB = f"{IMPACT_MOUNT}/last_job"
IMPACT_LATEST = f"{IMPACT_MOUNT}/latest"
ANALYZE_IMPACT = "analyze_impact"
IMPACT_DEADLINE_S = 10 * 60.0

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = PROJECT_DIR / "local" / "runtime" / "dos" / "impacts"

ImpactRunner = Callable[[dict, Path, dict], dict]


class ImpactDevice(Driver):
    device_id = "compute:impact-analysis"
    default_txn_timeout = 60.0

    def __init__(self, runner: ImpactRunner, artifact_root: Optional[Path] = None):
        self.runner = runner
        self.artifact_root = Path(artifact_root) if artifact_root else DEFAULT_ARTIFACT_ROOT
        self.last_error: Optional[str] = None

    def deadline_for(self, action: str) -> Optional[float]:
        return IMPACT_DEADLINE_S if action == ANALYZE_IMPACT else None

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        if action != ANALYZE_IMPACT:
            return f"unsupported action: {action}"
        forecast_id = args.get("forecast_id")
        if not isinstance(forecast_id, str) or not forecast_id:
            return "args.forecast_id is required"
        if self.kernel is None or self.kernel.namespace.try_read(f"{FORECAST_MOUNT}/{forecast_id}") is None:
            return f"unknown forecast: {forecast_id}"
        targets = args.get("targets")
        if not isinstance(targets, list) or not targets:
            return "args.targets must be a non-empty list"
        return None

    def dispatch(self, txn: PendingTxn) -> None:
        impact_id = f"impact_{txn.dispatched_seq:06d}"
        target = self.artifact_root / impact_id
        forecast_meta = dict(self.kernel.read(f"{FORECAST_MOUNT}/{txn.args['forecast_id']}").value)
        self.kernel.interrupt(
            self.device_id,
            {"kind": "job_started", "txn_id": txn.txn_id, "impact_id": impact_id, "forecast_id": txn.args["forecast_id"]},
        )
        threading.Thread(
            target=self._run_job,
            args=(txn, impact_id, target, forecast_meta),
            daemon=True,
            name=f"dos-{self.device_id}-{impact_id}",
        ).start()

    def _run_job(self, txn: PendingTxn, impact_id: str, target: Path, forecast_meta: dict) -> None:
        try:
            result = self.runner(txn.args, target, forecast_meta)
        except Exception as exc:  # noqa: BLE001 — job failure is evidence
            self.kernel.interrupt(self.device_id, {"kind": "job_error", "txn_id": txn.txn_id, "error": f"{type(exc).__name__}: {exc}"})
            return
        self.kernel.interrupt(
            self.device_id,
            {
                "kind": "job_done",
                "txn_id": txn.txn_id,
                "impact_id": impact_id,
                "forecast_id": txn.args["forecast_id"],
                "targets": txn.args.get("targets"),
                "result": result,
            },
        )

    def normalize(self, raw: object):
        kind = raw["kind"]
        if kind == "job_started":
            yield IMPACT_PENDING, {"txn_id": raw["txn_id"], "impact_id": raw["impact_id"], "forecast_id": raw["forecast_id"]}, time.time()
        elif kind == "job_done":
            impact_id = raw["impact_id"]
            meta = dict(raw["result"])
            meta.update({"id": impact_id, "txn_id": raw["txn_id"], "forecast_id": raw["forecast_id"], "targets": raw.get("targets") or []})
            yield IMPACT_PENDING, {}, time.time()
            yield IMPACT_LAST_JOB, {"txn_id": raw["txn_id"], "status": "done", "impact_id": impact_id}, time.time()
            yield f"{IMPACT_MOUNT}/{impact_id}", meta, time.time()
            yield IMPACT_LATEST, {"id": impact_id, "forecast_id": raw["forecast_id"]}, time.time()
        elif kind == "job_error":
            yield IMPACT_PENDING, {}, time.time()
            yield IMPACT_LAST_JOB, {"txn_id": raw["txn_id"], "status": "error", "error": raw["error"]}, time.time()
        else:
            self.last_error = f"unknown job frame: {kind}"

    def verify(self, txn: PendingTxn, read) -> str:
        snap = read(IMPACT_LAST_JOB)
        if snap is None:
            return "pending"
        evidence = snap.value
        if evidence.get("txn_id") != txn.txn_id:
            return "pending"
        if evidence.get("status") == "done":
            return "committed"
        if evidence.get("status") == "error":
            return f"failed: {evidence.get('error') or 'impact job failed'}"
        return "pending"


def mount_impact(kernel: Kernel, runner: ImpactRunner, artifact_root: Optional[Path] = None) -> ImpactDevice:
    device = ImpactDevice(runner=runner, artifact_root=artifact_root)
    kernel.mount(IMPACT_MOUNT, device)
    return device


def spawn_impact_auto(kernel: Kernel, cap_token: str, *, targets: list, sink: Optional[list] = None) -> None:
    """Stateless sweep: new committed forecast → standard target analysis."""
    events = sink if sink is not None else []

    def handler(ctx):
        latest = ctx.try_read(FORECAST_LATEST)
        if latest is None:
            return
        forecast_id = latest.value.get("id")
        if not forecast_id:
            return
        done = ctx.try_read(IMPACT_LATEST)
        if done is not None and done.value.get("forecast_id") == forecast_id:
            return  # this forecast already has its standard sweep
        result = ctx.act(cap_token, IMPACT_LATEST, ANALYZE_IMPACT, {"forecast_id": forecast_id, "targets": targets})
        events.append(f"analyze->{result.state}" + ("(reused)" if result.reused else ""))

    kernel.spawn(
        ProcessSpec(
            name="impact-auto",
            watches=(FORECAST_LATEST,),
            handler=handler,
            priority=4,
            budget_seconds=5.0,
            description="新预测落地 → 标准目标集淹没影响评估（无状态）",
        )
    )
