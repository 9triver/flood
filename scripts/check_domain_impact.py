"""Run the Domain OS forecast-to-impact vertical slice with real data."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from domain_os import CommandState, SqliteDomainStore
from domains.flood.forecast_domain import DEFAULT_ARTIFACT_ROOT, FORECAST_PRODUCT
from domains.flood.impact_domain import (
    IMPACT_PRODUCT,
    create_flood_impact_domain_system,
)
from domains.flood.runtime.boundary_flow import load_boundary_flow_rows


async def run(args: argparse.Namespace) -> dict[str, Any]:
    store = SqliteDomainStore(args.database) if args.database else None
    rows = load_boundary_flow_rows(args.csv)
    system = create_flood_impact_domain_system(
        rows=rows,
        store=store,
        artifact_root=args.artifact_root,
        evolution_run_id=args.run_id,
    )
    await system.start()
    steps = 0
    try:
        while system.forecast_system.evolution_driver.has_next and steps < args.max_steps:
            await system.advance()
            steps += 1
            assessments = system.runtime.products(product_type=IMPACT_PRODUCT)
            if assessments:
                assessment = assessments[-1]
                forecast = system.runtime.products(product_type=FORECAST_PRODUCT)[-1]
                command = system.runtime.commands()[-1]
                affected = dict(assessment.data.get("affected_object_ids") or {})
                return {
                    "steps": steps,
                    "observations": len(system.runtime.observations()),
                    "forecast_product_id": forecast.product_id,
                    "impact_product_id": assessment.product_id,
                    "input_refs": list(assessment.input_refs),
                    "command_id": command.command_id,
                    "command_state": command.state.value,
                    "status": assessment.data.get("status"),
                    "total_impacts": int(assessment.data.get("total_impacts") or 0),
                    "summary": dict(assessment.data.get("summary") or {}),
                    "affected_object_counts": {
                        object_type: len(ids)
                        for object_type, ids in affected.items()
                    },
                    "sample_impacts": list(assessment.data.get("impacts") or [])[:3],
                    "object_library_version": assessment.data.get(
                        "object_library_version"
                    ),
                    "artifacts": dict(assessment.artifacts),
                }
            commands = system.runtime.commands()
            if commands and commands[-1].state in {
                CommandState.FAILED,
                CommandState.REJECTED,
                CommandState.OUTCOME_UNKNOWN,
            }:
                command = commands[-1]
                raise RuntimeError(
                    f"domain command {command.command_id} ended in "
                    f"{command.state.value}: {command.error or 'unknown error'}"
                )
        raise RuntimeError(
            f"no impact assessment was generated after {steps} evolution steps"
        )
    finally:
        await system.stop()
        if store is not None:
            store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Domain OS forecast-to-impact vertical slice.",
    )
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--run-id",
        help="Stable evolution run ID; omitted by default to create a unique run.",
    )
    parser.add_argument("--max-steps", type=int, default=200)
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    return args


def main() -> None:
    result = asyncio.run(run(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
