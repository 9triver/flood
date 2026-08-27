from __future__ import annotations

from domain_os_mvp import ProcessSpec

from .hydrodynamic import RUN_FORECAST
from .paths import MODEL_PATH, latest_product_path, station_metric_path
from .telemetry import BOUNDARY_STATIONS


def spawn_flood_emergency_scenario(
    kernel,
    model_capability: str,
    *,
    boundaries: tuple[str, ...] | None = None,
    trigger_total_m3s: float = 230.0,
    window_hours: float = 24.0,
    sink: list | None = None,
) -> None:
    """Boundary telemetry above threshold starts one versioned forecast."""
    boundary_ids = tuple(boundaries or BOUNDARY_STATIONS)
    events = sink if sink is not None else []
    metric_paths = {
        station_id: station_metric_path(station_id, "flow_m3s")
        for station_id in boundary_ids
    }

    def handler(context) -> None:
        active = [
            item
            for item in kernel.operations({"awaiting_approval", "dispatched"})
            if item.resource_path == MODEL_PATH
        ]
        if active:
            return

        snapshots = {
            station_id: context.read(path)
            for station_id, path in metric_paths.items()
        }
        if any(snapshot is None for snapshot in snapshots.values()):
            return
        latest_values = {
            station_id: float(snapshot.value["value"])
            for station_id, snapshot in snapshots.items()
        }
        total = sum(latest_values.values())
        if total <= float(trigger_total_m3s):
            return

        newest_observed_at = max(
            snapshot.observed_at for snapshot in snapshots.values()
        )
        latest = context.read(latest_product_path("forecast"))
        if latest is not None:
            product = context.read(latest.value["ref"])
            if product is not None:
                covered = (
                    product.value.get("data") or {}
                ).get("input_last_observed_at")
                if covered is not None and float(covered) >= newest_observed_at:
                    return

        since = newest_observed_at - float(window_hours) * 3600.0
        stations = {}
        for station_id, path in metric_paths.items():
            stations[station_id] = [
                [observation.observed_at, float(observation.value["value"])]
                for observation in kernel.history(path, since=since)
            ]
        model = context.read(MODEL_PATH)
        if model is None:
            return
        result = context.act(
            model_capability,
            MODEL_PATH,
            RUN_FORECAST,
            {
                "stations": stations,
                "window_hours": float(window_hours),
                "total_m3s": total,
                "last_observed_at": newest_observed_at,
                "input_revision": max(
                    snapshot.revision for snapshot in snapshots.values()
                ),
            },
            expected_revision=model.revision,
        )
        events.append(result)

    kernel.spawn(
        ProcessSpec(
            name="flood-emergency-forecast-trigger",
            watches=tuple(metric_paths.values()),
            handler=handler,
        )
    )
