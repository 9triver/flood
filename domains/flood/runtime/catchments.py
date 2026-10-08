"""Static catchment metadata shared by runoff parameters and the workbench."""
from __future__ import annotations

import json
import math
from functools import lru_cache

from .common import OBJECT_LIBRARY_FILES, OBJECTS_DIR


@lru_cache(maxsize=1)
def longtan_catchment() -> dict:
    path = OBJECTS_DIR / OBJECT_LIBRARY_FILES["Catchment"]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matches = [row for row in rows if row["catchment_id"] == "longtan_upstream" and row["reservoir_id"] == "longtan"]
    if len(matches) != 1:
        raise ValueError("Exactly one Longtan upstream catchment is required")
    row = matches[0]
    area = float(row["area_km2"])
    if not math.isfinite(area) or area <= 0:
        raise ValueError("Longtan catchment area must be positive and finite")
    return {key: row[key] for key in (
        "catchment_id", "name", "reservoir_id", "reservoir_name",
        "rainfall_field", "area_km2",
    )}
