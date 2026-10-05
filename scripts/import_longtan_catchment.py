"""Import the supplied Longtan catchment without simplifying its boundary.

Run with: uv run --no-sync --with pyshp python scripts/import_longtan_catchment.py
Only the import step needs pyshp; serving the generated object does not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import shapefile
from pyproj import CRS, Geod, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.validation import explain_validity


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path,
                        default=ROOT / "集水区/龙潭水库上游集水区.shp")
    args = parser.parse_args()
    source = args.source
    crs = CRS.from_wkt(source.with_suffix(".prj").read_text())
    with shapefile.Reader(str(source), encoding="utf-8") as reader:
        if len(reader) != 1:
            raise ValueError("Expected one Longtan catchment feature")
        record = reader.shapeRecord(0)
        geometry = shape(record.shape.__geo_interface__)
        attributes = record.record.as_dict()
    if geometry.geom_type != "Polygon" or geometry.is_empty or not geometry.is_valid:
        raise ValueError(f"Invalid catchment polygon: {explain_validity(geometry)}")
    lonlat = transform(Transformer.from_crs(crs, 4326, always_xy=True).transform, geometry)
    geodesic_area = abs(Geod(ellps="WGS84").geometry_area_perimeter(lonlat)[0]) / 1e6
    area = float(attributes["area"])
    if not area > 0 or abs(area - geodesic_area) / area > .01:
        raise ValueError("Source area differs from geodesic area by more than 1%")
    row = {
        "catchment_id": "longtan_upstream",
        "name": "龙潭水库上游集水区",
        "reservoir_id": "longtan",
        "reservoir_name": "龙潭水库",
        "river_id": "shanhu",
        "rainfall_field": "reservoir_rainfall_mm",
        "area_km2": area,
        "area_source": "源数据 area 字段；元数据注明按 CGCS2000 高斯投影计算，单位平方千米",
        "geodesic_area_km2": round(geodesic_area, 8),
        "geometry_crs": "EPSG:4326",
        "geometry_type": lonlat.geom_type,
        "geometry": json.dumps(mapping(lonlat), ensure_ascii=False),
        "source_crs": crs.to_string(),
        "source_file": source.name,
        "source_sha256": {
            suffix: hashlib.sha256(source.with_suffix(suffix).read_bytes()).hexdigest()
            for suffix in (".shp", ".dbf", ".prj", ".shp.xml")
        },
        "source_record_id": attributes["Id"],
        "boundary_review_status": "source_boundaries_differ",
        "data_note": "原始栅格转面集水区，经坐标转换后保留全部顶点；与珊瑚河原始流域边界有约2.56%面积差异，保留独立范围，不作裁剪或严格包含推断。",
    }
    target = ROOT / "domains/flood/data/objects/catchment.jsonl"
    target.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Imported {row['name']}: {area:.8f} km² -> {target}")


if __name__ == "__main__":
    main()
