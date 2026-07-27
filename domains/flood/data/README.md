# Flood domain data

This directory contains only versioned inputs and queryable domain data.

- `objects/`: canonical JSONL object library used by the repository.
- `mock/`: deterministic input templates used by the evolution service.
- `sources/`: compact external source snapshots required by the object builder.

Runtime observations, forecasts, impacts, routes, traces, and GeoJSON caches belong
under `local/runtime/flood/workspaces/`. Shared rebuildable caches belong under
`local/runtime/flood/cache/`. Large downloads and intermediate source rasters belong
under `local/source_data/`.

Each successful forecast is archived in its workspace as `forecasts/vNNN`, while
`forecasts/latest` remains the compatibility path used by the live map. `InundationForecastCell`
geometries are derived from the shared mesh on demand and are not persisted. Successful
CNN temporary input/output directories are removed; failed runs keep them for diagnosis.
Evolution workspaces are retained by default. Set `FLOOD_WORKSPACE_RETENTION_COUNT` to
a positive number only when automatic pruning is explicitly wanted.

Legacy design-flood max-depth scenarios are reference material, not live forecasts.
Local copies are archived under `local/reference_data/flood/design_flood_scenarios/`.
