from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pyproj import Transformer

from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.linear_inundation import WetCellIndex
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.road_routes import build_road_routes


# Generate fixtures in metres in the mesh CRS, independent of the matcher.
TO_METRES = Transformer.from_crs(4326, 4546, always_xy=True)
TO_WGS84 = Transformer.from_crs(4546, 4326, always_xy=True)
ORIGIN = TO_METRES.transform(111.36, 24.36)


def lonlat(x, y):
    return TO_WGS84.transform(ORIGIN[0] + x, ORIGIN[1] + y)


def road(points, road_id="road1", **extra):
    return {"road_id": road_id, "name": "测试路", "ref": "X706", **extra,
            "geometry": json.dumps({"type": "LineString", "coordinates": [lonlat(*p) for p in points]})}


def cell(cell_id="wet", bounds=(-5, -5, 5, 5), depth=0.8):
    x1, y1, x2, y2 = bounds
    lon, lat = lonlat((x1 + x2) / 2, (y1 + y2) / 2)
    return {
        "forecast_id": "test", "forecast_cell_id": f"test_{cell_id}", "mesh_cell_id": cell_id,
        "centroid_lon": lon, "centroid_lat": lat, "depth_m": depth, "velocity_mps": 0.2,
        "risk_level": "medium", "geometry_crs": "EPSG:4326",
        "geometry": json.dumps({"type": "Polygon", "coordinates": [[
            lonlat(x1, y1), lonlat(x2, y1), lonlat(x2, y2), lonlat(x1, y2), lonlat(x1, y1),
        ]]}),
    }


class Resolver:
    def __init__(self, roads):
        self.rows = {"Road": roads, "RoadRoute": build_road_routes(roads)[0]}

    def query(self, object_type, filters=None):
        return self.rows.get(object_type, [])


def analyze(roads, cells, target_type="Road", distance=10):
    with (
        patch("domains.flood.runtime.impact_analysis.query_forecast_cells", return_value=cells),
        patch("domains.flood.runtime.impact_analysis.forecast_time_context", return_value={}),
    ):
        return analyze_inundation_impacts(Resolver(roads), target_type=target_type, max_distance_m=distance)


class LinearInundationTest(unittest.TestCase):
    def test_long_line_crosses_cell_between_distant_vertices(self):
        result = analyze([road([(-4000, 0), (4000, 0)])], [cell()])
        hit = result["impacts"][0]
        self.assertEqual("forecast_overlap", hit["impact_status"])
        self.assertEqual(["wet"], hit["intersecting_mesh_cell_ids"])
        self.assertAlmostEqual(10, hit["overlap_length_m"], delta=0.01)
        self.assertEqual(0, result["total_nearby"])

    def test_large_cell_overlap_does_not_require_nearby_centroid(self):
        result = analyze([road([(-100, 40), (100, 40)])], [cell(bounds=(-50, -50, 50, 50))], distance=0)
        self.assertEqual(1, result["total_impacts"])
        self.assertAlmostEqual(100, result["impacts"][0]["overlap_length_m"], delta=0.01)

    def test_nearby_cell_is_not_counted_as_inundation(self):
        result = analyze([road([(-100, 12), (100, 12)])], [cell()])
        self.assertEqual(0, result["total_impacts"])
        self.assertEqual([], result["affected_object_ids"]["Road"])
        self.assertEqual(1, result["total_nearby"])
        hit = result["nearby_impacts"][0]
        self.assertEqual("nearby_flood", hit["impact_status"])
        self.assertFalse(hit["directly_inundated"])
        self.assertAlmostEqual(7, hit["nearest_distance_m"], delta=0.01)
        self.assertEqual(0, result["road_route_coverage"]["affected_route_count"])

    def test_deeper_nearby_cell_cannot_replace_intersecting_depth(self):
        result = analyze([road([(-100, 0), (100, 0)])], [
            cell("crossing", depth=0.2), cell("nearby", (-5, 6, 5, 10), depth=2),
        ])
        hit = result["impacts"][0]
        self.assertEqual(0.2, hit["depth_m"])
        self.assertEqual("crossing", hit["mesh_cell_id"])
        self.assertEqual(["nearby"], hit["nearby_mesh_cell_ids"])

    def test_all_wet_cells_are_retained_above_old_subsampling_limit(self):
        far = cell("far", (1000, 1000, 1010, 1010))
        cells = [{**far, "mesh_cell_id": f"far-{i}"} for i in range(14002)]
        cells[1] = cell("must_not_be_dropped")
        result = analyze([road([(-100, 0), (100, 0)])], cells)
        self.assertEqual(14002, result["linear_analysis"]["indexed_wet_cell_count"])
        self.assertEqual(["must_not_be_dropped"], result["impacts"][0]["intersecting_mesh_cell_ids"])

    def test_polygon_hole_is_not_flooded(self):
        wet = cell(bounds=(-30, -30, 30, 30))
        geometry = json.loads(wet["geometry"])
        geometry["coordinates"].append([lonlat(*p) for p in [(-15, -15), (-15, 15), (15, 15), (15, -15), (-15, -15)]])
        wet["geometry"] = json.dumps(geometry)
        result = analyze([road([(-1, 0), (1, 0)])], [wet])
        self.assertEqual(0, result["total_impacts"])
        self.assertEqual(0, result["total_nearby"])

    def test_disconnected_multiline_does_not_join_across_gap(self):
        row = road([(-100, 0), (-20, 0)])
        row["geometry"] = json.dumps({"type": "MultiLineString", "coordinates": [
            [lonlat(-100, 0), lonlat(-20, 0)], [lonlat(20, 0), lonlat(100, 0)],
        ]})
        result = analyze([row], [cell()])
        self.assertEqual(0, result["total_impacts"])
        self.assertEqual(0, result["total_nearby"])

    def test_inside_polygon_and_boundary_touch_are_detected(self):
        rows = [road([(-1, 0), (1, 0)], "inside"), road([(-10, -10), (-5, -5)], "touch")]
        result = analyze(rows, [cell()], distance=0)
        self.assertEqual({"inside", "touch"}, set(result["affected_object_ids"]["Road"]))
        touch = next(row for row in result["impacts"] if row["object_id"] == "touch")
        self.assertEqual(0, touch["overlap_length_m"])

    def test_overlapping_cells_do_not_double_count_length(self):
        result = analyze([road([(-100, 0), (100, 0)])], [cell("a"), cell("b")])
        self.assertAlmostEqual(10, result["impacts"][0]["overlap_length_m"], delta=0.01)
        self.assertEqual(2, result["impacts"][0]["intersecting_cell_count"])

    def test_threshold_and_proximity_threshold_are_independent(self):
        rows = [road([(-100, 0), (100, 0)], "dry"), road([(-100, 20), (100, 20)], "far")]
        result = analyze(rows, [cell(depth=0.149)])
        self.assertEqual(0, result["total_impacts"])
        self.assertEqual(0, result["total_nearby"])
        result = analyze(rows, [cell(depth=0.15)])
        self.assertEqual(["dry"], result["affected_object_ids"]["Road"])
        self.assertEqual(0, result["total_nearby"])

    def test_missing_geometries_are_explicitly_unassessed(self):
        row = road([(-100, 0), (100, 0)])
        wet = cell()
        wet.pop("geometry")
        result = analyze([row], [wet])
        self.assertEqual("partial", result["status"])
        self.assertEqual(["wet"], result["linear_analysis"]["skipped_cell_ids"])
        row["geometry"] = ""
        result = analyze([row], [cell()])
        self.assertEqual("partial", result["status"])
        self.assertEqual("road1", result["linear_analysis"]["unassessed_objects"][0]["object_id"])

    def test_bridge_and_tunnel_road_surfaces_remain_unverified(self):
        for flag in ("bridge_flag", "tunnel_flag"):
            result = analyze([road([(-100, 0), (100, 0)], **{flag: True})], [cell()])
            hit = result["impacts"][0]
            self.assertEqual("structure_overlap_unverified", hit["impact_status"])
            self.assertFalse(hit["directly_inundated"])

    def test_route_counts_keep_nearby_members_separate(self):
        rows = [road([(-100, 0), (100, 0)], "crossing"), road([(-100, 12), (100, 12)], "nearby")]
        result = analyze(rows, [cell()], target_type="RoadRoute")
        route = result["impacts"][0]
        self.assertEqual(2, route["recorded_segment_count"])
        self.assertEqual(1, route["affected_segment_count"])
        self.assertEqual(1, route["nearby_segment_count"])
        self.assertEqual(["crossing"], route["affected_road_ids"])
        self.assertEqual(["nearby"], route["nearby_road_ids"])
        result = analyze(rows[1:], [cell()], target_type="RoadRoute")
        self.assertEqual(0, result["total_impacts"])
        self.assertEqual(1, result["total_nearby"])

    def test_x706_regression_20260704_1030(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/x706_inundation_20260704_1030.json").read_text())
        resolver = FloodRepository()
        with (
            patch("domains.flood.runtime.impact_analysis.query_forecast_cells", return_value=fixture["cells"]),
            patch("domains.flood.runtime.impact_analysis.forecast_time_context", return_value={
                "valid_at": fixture["analysis_time_at"], "valid_from": fixture["valid_from"],
            }),
        ):
            result = analyze_inundation_impacts(resolver, forecast_id="v004", target_type="RoadRoute", time_h=23.5)
        route = next(row for row in result["impacts"] if row["ref"] == "X706")
        self.assertEqual(fixture["analysis_time_at"], result["analysis_time_at"])
        self.assertEqual(9, route["recorded_segment_count"])
        self.assertEqual(1, route["affected_segment_count"])
        hit = route["segment_impacts"][0]
        self.assertEqual(fixture["road_id"], hit["object_id"])
        self.assertEqual(["383262", "383265", "383269"], hit["intersecting_mesh_cell_ids"])
        self.assertEqual(0.213, hit["depth_m"])
        self.assertAlmostEqual(22.7, hit["overlap_length_m"], delta=0.1)
        specified = next(row for row in fixture["cells"] if row["mesh_cell_id"] == "384863")
        index = WetCellIndex([specified], 0.15)
        evidence = index.match(resolver.query_by_id("Road", fixture["road_id"]), 30)
        self.assertEqual("nearby_flood", evidence["status"])
        self.assertAlmostEqual(19.3, evidence["nearest_distance_m"], delta=0.2)


if __name__ == "__main__":
    unittest.main()
