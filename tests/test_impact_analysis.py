from __future__ import annotations

import json
import unittest

from domains.flood.runtime.impact_analysis import (
    affected_object_ids,
    analyze_linear_objects,
    analyze_point_objects,
)


class StaticResolver:
    def __init__(self, rows_by_type):
        self.rows_by_type = rows_by_type

    def query(self, object_type):
        return self.rows_by_type.get(object_type, [])


class ImpactAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.cell = {
            "forecast_cell_id": "cell-1",
            "mesh_cell_id": "mesh-1",
            "centroid_lon": 111.30001,
            "centroid_lat": 24.40001,
            "depth_m": 0.8,
            "velocity_mps": 0.4,
            "risk_level": "medium",
        }

    def test_point_impact_uses_object_location(self):
        resolver = StaticResolver({
            "Facility": [{
                "facility_id": "facility-1",
                "name": "测试学校",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
        })

        impacts = analyze_point_objects(resolver, "Facility", [self.cell], 0.15, 10)

        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0]["longitude"], 111.3)
        self.assertEqual(impacts[0]["latitude"], 24.4)

    def test_linear_impact_uses_matching_sample_location(self):
        resolver = StaticResolver({
            "Road": [{
                "road_id": "road-1",
                "name": "测试道路",
                "geometry": json.dumps({
                    "type": "LineString",
                    "coordinates": [[111.2, 24.3], [111.3, 24.4]],
                }),
            }],
        })

        impacts = analyze_linear_objects(resolver, "Road", [self.cell], 0.15, 10)

        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0]["longitude"], 111.3)
        self.assertEqual(impacts[0]["latitude"], 24.4)

    def test_affected_ids_are_not_truncated(self):
        impacts = [
            {"object_type": "Road", "object_id": str(index)}
            for index in range(25)
        ]

        result = affected_object_ids(["Road"], impacts)

        self.assertEqual(len(result["Road"]), 25)


if __name__ == "__main__":
    unittest.main()
