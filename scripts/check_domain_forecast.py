"""Run the Domain OS flood forecast vertical slice with the real CNN model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from domain_os import CommandState, SqliteDomainStore
from domains.flood.forecast_domain import (
    DEFAULT_ARTIFACT_ROOT,
    FORECAST_INPUT_PRODUCT,
    FORECAST_PRODUCT,
    create_flood_forecast_domain_system,
)
from domains.flood.runtime.boundary_flow import load_boundary_flow_rows


async def run(args: argparse.Namespace) -> dict[str, Any]:
    store = SqliteDomainStore(args.database) if args.database else None
    rows = load_boundary_flow_rows(args.csv)
    system = create_flood_forecast_domain_system(
        rows=rows,
        store=store,
        artifact_root=args.artifact_root,
        evolution_run_id=args.run_id,
    )
    await system.start()
    steps = 0
    try:
        while system.evolution_driver.has_next and steps < args.max_steps:
            await system.advance()
            steps += 1
            forecasts = system.runtime.products(product_type=FORECAST_PRODUCT)
            if forecasts:
                forecast = forecasts[-1]
                input_product = system.runtime.products(
                    product_type=FORECAST_INPUT_PRODUCT,
                )[-1]
                command = system.runtime.commands()[-1]
                return {
                    "steps": steps,
                    "observations": len(system.runtime.observations()),
                    "forecast_input_product_id": input_product.product_id,
                    "forecast_product_id": forecast.product_id,
                    "valid_from": forecast.valid_from.isoformat(),
                    "valid_to": forecast.valid_to.isoformat(),
                    "command_id": command.command_id,
                    "command_state": command.state.value,
                    "forecast": dict(forecast.data),
                    "artifacts": dict(forecast.artifacts),
                }
            commands = system.runtime.commands()
            if commands and commands[-1].state in {
                CommandState.FAILED,
                CommandState.REJECTED,
                CommandState.OUTCOME_UNKNOWN,
            }:
                command = commands[-1]
                raise RuntimeError(
                    f"forecast command {command.command_id} ended in "
                    f"{command.state.value}: {command.error or 'unknown error'}"
                )
        raise RuntimeError(
            f"no forecast was generated after {steps} evolution steps"
        )
    finally:
        await system.stop()
        if store is not None:
            store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Domain OS flood forecast vertical slice.",
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
