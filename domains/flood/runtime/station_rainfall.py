from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from functools import lru_cache
from typing import Any

from .common import OBJECTS_DIR


STATION_RAINFALL_METHOD = "synthetic_spatiotemporal_v1"


@lru_cache(maxsize=1)
def meteorological_stations() -> tuple[dict[str, Any], ...]:
    path = OBJECTS_DIR / "station.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return tuple(
        row for row in rows
        if row.get("station_type") == "meteorological"
    )


def station_rainfall_column(station_id: str) -> str:
    return f"rainfall_{station_id}_mm"


def station_rainfall_columns() -> tuple[str, ...]:
    return tuple(
        station_rainfall_column(str(station["station_id"]))
        for station in meteorological_stations()
    )


def extend_boundary_flow_csv(content: bytes, source_seed: str) -> bytes:
    """Add deterministic per-station rainfall columns to a boundary-flow CSV."""
    had_bom = content.startswith(b"\xef\xbb\xbf")
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = list(reader.fieldnames or ())
    station_columns = list(station_rainfall_columns())
    present = [column for column in station_columns if column in fieldnames]
    if present:
        if len(present) != len(station_columns):
            raise ValueError("气象站雨量列必须完整提供")
        return content

    rows = list(reader)
    stations = meteorological_stations()
    for index, row in enumerate(rows):
        rainfall = _number(row.get("rainfall_mm"))
        station_values = disaggregate_station_rainfall(
            rainfall,
            stations,
            source_seed=source_seed,
            time_index=index,
        )
        for station, value in zip(stations, station_values):
            row[station_rainfall_column(str(station["station_id"]))] = _format_number(value)

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[*fieldnames, *station_columns],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    encoded = output.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if had_bom else encoded


def disaggregate_station_rainfall(
    rainfall_mm: float,
    stations: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    source_seed: str,
    time_index: int,
) -> list[float]:
    rainfall = max(0.0, float(rainfall_mm or 0))
    if not stations or rainfall == 0:
        return [0.0] * len(stations)

    coordinates = [
        (float(station["longitude"]), float(station["latitude"]))
        for station in stations
    ]
    mean_lon = sum(point[0] for point in coordinates) / len(coordinates)
    mean_lat = sum(point[1] for point in coordinates) / len(coordinates)
    cos_lat = math.cos(math.radians(mean_lat))
    centered = [
        ((lon - mean_lon) * cos_lat, lat - mean_lat)
        for lon, lat in coordinates
    ]
    scale = max(
        max(abs(x), abs(y)) for x, y in centered
    ) or 1.0
    normalized = [(x / scale, y / scale) for x, y in centered]

    seed_phase = (_stable_unit_interval(source_seed) * 2.0 - 1.0) * math.pi
    phase = seed_phase + time_index * 0.085
    direction = seed_phase * 0.65 + time_index * 0.045
    raw_field = []
    for station, (x, y) in zip(stations, normalized):
        station_bias = _stable_unit_interval(
            f"{source_seed}:{station['station_id']}"
        ) * 2.0 - 1.0
        spatial_gradient = math.cos(direction) * x + math.sin(direction) * y
        smooth_wave = math.sin(phase + 1.7 * x - 1.3 * y)
        raw_field.append(
            0.62 * spatial_gradient
            + 0.28 * smooth_wave
            + 0.10 * station_bias
        )

    mean_field = sum(raw_field) / len(raw_field)
    centered_field = [value - mean_field for value in raw_field]
    field_scale = max(abs(value) for value in centered_field) or 1.0
    normalized_field = [value / field_scale for value in centered_field]
    spread = _relative_spread(rainfall)
    values = [
        round(rainfall * (1.0 + spread * value), 3)
        for value in normalized_field
    ]

    target_total = round(rainfall * len(values), 3)
    residual = round(target_total - sum(values), 3)
    correction_index = max(range(len(values)), key=values.__getitem__)
    values[correction_index] = round(values[correction_index] + residual, 3)
    return values


def station_rainfall_from_csv_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = []
    for station in meteorological_stations():
        station_id = str(station["station_id"])
        column = station_rainfall_column(station_id)
        raw = row.get(column)
        if raw in (None, ""):
            continue
        values.append({
            "station_id": station_id,
            "name": str(station.get("name") or station_id),
            "rainfall_mm": round(_number(raw), 3),
            "derivation_method": STATION_RAINFALL_METHOD,
        })
    return values


def _relative_spread(rainfall_mm: float) -> float:
    if rainfall_mm < 2.0:
        return 0.35
    if rainfall_mm < 20.0:
        return 0.25
    return 0.18


def _stable_unit_interval(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float((1 << 64) - 1)


def _number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid numeric value: {value!r}")
    return number


def _format_number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"
