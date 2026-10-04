"""Sub-basin rainfall fields and the area-weighted display summary."""

BASIN_AREAS_KM2 = {"interval1": 381.0, "interval2": 85.0, "reservoir": 36.0}
BASIN_RAINFALL_COLUMNS = {key: f"{key}_rainfall_mm" for key in BASIN_AREAS_KM2}
REQUIRED_RAINFALL_COLUMNS = ("time_period_end", *BASIN_RAINFALL_COLUMNS.values())


def display_rainfall_mm(row: dict) -> float:
    """Area-weighted mean for telemetry; never used as basin forcing."""
    return sum(
        float(row[BASIN_RAINFALL_COLUMNS[key]]) * area
        for key, area in BASIN_AREAS_KM2.items()
    ) / sum(BASIN_AREAS_KM2.values())
