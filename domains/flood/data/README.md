# Flood domain data

This directory contains only versioned inputs and queryable domain data.

- `objects/`: canonical JSONL object library used by the repository.
- `mock/`: deterministic input templates used by the evolution service.

`mock/rainfall.csv` contains hourly period-end times and three simulated basin
rainfall depths (mm per period): `interval1_rainfall_mm`, `interval2_rainfall_mm`,
and `reservoir_rainfall_mm`. Relative to the previous baseline rainfall, their
intensity factors and delays are 1.00/+1h, 0.85/+2h, and 1.10/0h respectively.
These fixed demo assumptions are independent of runoff routing lag. Each basin
uses its own rainfall input; the area-weighted mean is only a display summary.

Runtime observations, forecasts, impacts, routes, traces, and GeoJSON caches belong
under `local/runtime/flood/workspaces/`. Shared rebuildable caches belong under
`local/runtime/flood/cache/`. The runtime does not require the original GIS/Excel
source package.

Each successful forecast is archived in its workspace as `forecasts/vNNN`, while
`forecasts/latest` remains the compatibility path used by the live map. `InundationForecastCell`
geometries are derived from the shared mesh on demand and are not persisted. Successful
Hydrodynamic model temporary input/output directories are removed; failed runs keep them for diagnosis.
Evolution workspaces are retained by default. Set `FLOOD_WORKSPACE_RETENTION_COUNT` to
a positive number only when automatic pruning is explicitly wanted.

Legacy design-flood max-depth scenarios are reference material, not live forecasts.
Local copies are archived under `local/reference_data/flood/design_flood_scenarios/`.
