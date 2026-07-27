from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from domains.flood.runtime.impact_analysis import (
    affected_object_ids,
    analyze_bridge_objects,
    analyze_inundation_impacts,
    analyze_linear_objects,
    analyze_point_objects,
    mark_bridge_approach_impacts,
    propagate_bridge_impacts,
)


class StaticResolver:
    def __init__(self, rows_by_type):
        self.rows_by_type = rows_by_type

    def query(self, object_type, filters=None):
        rows = self.rows_by_type.get(object_type, [])
        if not filters:
            return rows
        return [
            row for row in rows
            if all(row.get(key) == value for key, value in filters.items())
        ]


def flood_cell(cell_id, lon, lat, depth):
    offset = 0.0001
    return {
        "forecast_cell_id": f"cell-{cell_id}",
        "mesh_cell_id": f"mesh-{cell_id}",
        "centroid_lon": lon,
        "centroid_lat": lat,
        "depth_m": depth,
        "velocity_mps": 0.4,
        "risk_level": "medium",
        "geometry": json.dumps({
            "type": "Polygon",
            "coordinates": [[
                [lon - offset, lat - offset],
                [lon + offset, lat - offset],
                [lon, lat + offset],
                [lon - offset, lat - offset],
            ]],
        }),
    }


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

    def test_facility_impact_preserves_type_for_domain_icon(self):
        resolver = StaticResolver({
            "Facility": [{
                "facility_id": "facility-1",
                "name": "测试学校",
                "facility_type": "school",
                "subtype": "小学",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
        })

        impacts = analyze_point_objects(resolver, "Facility", [self.cell], 0.15, 10)

        self.assertEqual(impacts[0]["facility_type"], "school")
        self.assertEqual(impacts[0]["subtype"], "小学")

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

    def test_time_slice_result_uses_absolute_forecast_time(self):
        resolver = StaticResolver({
            "Facility": [{
                "facility_id": "facility-1",
                "name": "测试学校",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
        })
        cell = {
            **self.cell,
            "forecast_id": "v003",
            "lead_time_h": 1.5,
        }
        with patch(
            "domains.flood.runtime.impact_analysis.query_forecast_cells",
            return_value=[cell],
        ), patch(
            "domains.flood.runtime.impact_analysis.forecast_time_context",
            return_value={
                "forecast_time": "2026-07-03T20:00:00+08:00",
                "valid_from": "2026-07-03T20:00:00+08:00",
                "valid_to": "2026-07-04T20:00:00+08:00",
                "valid_at": "2026-07-03T21:30:00+08:00",
            },
        ):
            result = analyze_inundation_impacts(
                resolver,
                forecast_id="latest",
                target_type="Facility",
                time_h=1.5,
            )

        self.assertEqual(1.5, result["time_h"])
        self.assertEqual("2026-07-03T21:30:00+08:00", result["analysis_time_at"])
        self.assertIn("2026-07-03T21:30:00+08:00", result["basis"])
        self.assertIn("预测 +1.500 h", result["basis"])

    def test_affected_ids_are_not_truncated(self):
        impacts = [
            {"object_type": "Road", "object_id": str(index)}
            for index in range(25)
        ]

        result = affected_object_ids(["Road"], impacts)

        self.assertEqual(len(result["Road"]), 25)

    def test_bridge_uses_polygon_influence_zone_and_is_not_directly_inundated(self):
        resolver = StaticResolver({
            "Bridge": [{
                "bridge_id": "bridge-1",
                "name": "测试桥梁",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
            "River": [{
                "geometry": json.dumps({
                    "type": "LineString",
                    "coordinates": [[111.299, 24.4], [111.301, 24.4]],
                }),
            }],
        })
        cells = [
            flood_cell("north", 111.3, 24.4003, 0.4),
            flood_cell("south", 111.3, 24.3997, 0.8),
        ]

        impacts = analyze_bridge_objects(resolver, cells, 0.15, 80)

        self.assertEqual(len(impacts), 1)
        impact = impacts[0]
        self.assertFalse(impact["directly_inundated"])
        self.assertEqual(impact["basis"], "bridge_approach_inundated")
        self.assertEqual(impact["passability_status"], "likely_impassable")
        self.assertEqual(impact["affected_side_count"], 2)
        self.assertEqual(impact["nearby_max_depth_m"], 0.8)
        self.assertEqual(impact["depth_basis"], "nearby_floodplain_forecast")

    def test_bridge_single_bank_impact_requires_inspection(self):
        resolver = StaticResolver({
            "Bridge": [{
                "bridge_id": "bridge-1",
                "name": "测试桥梁",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
            "River": [{
                "geometry": json.dumps({
                    "type": "LineString",
                    "coordinates": [[111.299, 24.4], [111.301, 24.4]],
                }),
            }],
        })

        impacts = analyze_bridge_objects(
            resolver,
            [flood_cell("north", 111.3, 24.4003, 0.4)],
            0.15,
            80,
        )

        self.assertEqual(impacts[0]["basis"], "bridge_influence_zone")
        self.assertEqual(impacts[0]["passability_status"], "inspection_required")
        self.assertEqual(impacts[0]["affected_side_count"], 1)

    def test_bridge_distance_uses_cell_polygon_instead_of_centroid(self):
        resolver = StaticResolver({
            "Bridge": [{
                "bridge_id": "bridge-1",
                "name": "测试桥梁",
                "longitude": 111.3,
                "latitude": 24.4,
            }],
        })
        cell = flood_cell("east", 111.301, 24.4, 0.5)
        cell["geometry"] = json.dumps({
            "type": "Polygon",
            "coordinates": [[
                [111.3006, 24.3999],
                [111.3014, 24.3999],
                [111.3014, 24.4001],
                [111.3006, 24.3999],
            ]],
        })

        impacts = analyze_bridge_objects(resolver, [cell], 0.15, 80)

        self.assertEqual(len(impacts), 1)
        self.assertLess(impacts[0]["distance_m"], 80)

    def test_linked_road_impact_marks_bridge_approach_impassable(self):
        resolver = StaticResolver({
            "BridgeRoadLink": [{
                "bridge_id": "bridge-1",
                "road_id": "road-1",
                "validation_status": "accepted",
            }],
        })
        bridge = {
            "object_type": "Bridge",
            "object_id": "bridge-1",
            "longitude": 111.3,
            "latitude": 24.4,
            "passability_status": "inspection_required",
        }
        road = {
            "object_type": "Road",
            "object_id": "road-1",
            "longitude": 111.3001,
            "latitude": 24.4,
            "directly_inundated": True,
        }

        mark_bridge_approach_impacts(resolver, [bridge], [road], 80)

        self.assertEqual(bridge["basis"], "bridge_approach_inundated")
        self.assertEqual(bridge["passability_status"], "likely_impassable")
        self.assertEqual(bridge["related_road_ids"], ["road-1"])

    def test_bridge_impact_propagates_to_linked_road(self):
        resolver = StaticResolver({
            "Road": [{"road_id": "road-1", "name": "桥上路段"}],
            "BridgeRoadLink": [{
                "bridge_road_link_id": "link-1",
                "bridge_id": "bridge-1",
                "road_id": "road-1",
                "validation_status": "accepted",
            }],
        })
        bridge_impacts = [{
            "object_type": "Bridge",
            "object_id": "bridge-1",
            "name": "测试桥梁",
            "risk_level": "high",
            "depth_m": 0.6,
            "velocity_mps": 0.3,
            "distance_m": 2.0,
            "forecast_cell_id": "cell-1",
            "mesh_cell_id": "mesh-1",
            "longitude": 111.3,
            "latitude": 24.4,
        }]

        impacts = propagate_bridge_impacts(resolver, bridge_impacts, [])

        self.assertEqual(len(impacts), 1)
        self.assertEqual(impacts[0]["object_id"], "road-1")
        self.assertEqual(impacts[0]["basis"], "bridge_dependency")
        self.assertFalse(impacts[0]["directly_inundated"])
        self.assertEqual(impacts[0]["passability_status"], "inspection_required")

    def test_direct_road_impact_is_not_duplicated_by_bridge_link(self):
        resolver = StaticResolver({
            "Road": [{"road_id": "road-1", "name": "桥上路段"}],
            "BridgeRoadLink": [{
                "bridge_road_link_id": "link-1",
                "bridge_id": "bridge-1",
                "road_id": "road-1",
                "validation_status": "accepted",
            }],
        })
        bridge_impacts = [{
            "object_type": "Bridge",
            "object_id": "bridge-1",
            "name": "测试桥梁",
        }]
        direct_road = {
            "object_type": "Road",
            "object_id": "road-1",
            "basis": "line_sample_nearest_cell",
        }

        propagated = propagate_bridge_impacts(resolver, bridge_impacts, [direct_road])

        self.assertEqual(propagated, [])
        self.assertEqual(direct_road["related_bridge_ids"], ["bridge-1"])
        self.assertTrue(direct_road["bridge_dependency"])


if __name__ == "__main__":
    unittest.main()
