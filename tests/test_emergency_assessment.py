import json
import unittest
from unittest.mock import patch

from domains.flood.runtime import forecast
from tests.test_linear_inundation import Resolver, road, cell, analyze


class EmergencyAssessmentTests(unittest.TestCase):
    def assess(self, roads, cells):
        run = {"forecast_id": "v001", "status": "completed", "max_depth_m": 0.8}
        with (
            patch.object(forecast, "read_forecast_runs", return_value=[run]),
            patch.object(forecast, "read_cached_emergency_cycle", return_value=None),
            patch.object(forecast, "write_cached_emergency_cycle"),
            patch.object(forecast, "hydrology_inputs_from_forecast", return_value={}),
            patch("domains.flood.runtime.impact_analysis.query_forecast_cells", return_value=cells) as read,
            patch("domains.flood.runtime.impact_analysis.forecast_time_context", return_value={}),
            patch.object(forecast, "run_flood_forecast", side_effect=AssertionError("assessment must not run CNN")),
        ):
            result = forecast.assess_flood_emergency(Resolver(roads))
        self.assertEqual(read.call_args.args[0], {"forecast_id": "v001"})
        return result

    def test_long_crossings_and_nearby_roads_agree_with_impact_analysis(self):
        roads = [road([(-4000, 0), (4000, 0)], "crossing"),
                 road([(-1, 50), (1, 50)], "far"), road([(-1, 12), (1, 12)], "nearby")]
        expected = analyze(roads, [cell()])
        result = self.assess(roads, [cell()])
        self.assertEqual([r["object_id"] for r in result["road_impacts"]], ["crossing"])
        self.assertEqual(result["impact_analysis"]["total_impacts"], expected["total_impacts"])
        self.assertEqual(result["impact_analysis"]["total_nearby"], 1)
        self.assertEqual(result["analysis_view"], "envelope")

    def test_complete_inventory_is_not_truncated_to_recommendation_count(self):
        roads = [road([(-20, 0), (20, 0)], f"r{i}") for i in range(12)]
        result = self.assess(roads, [cell()])
        self.assertEqual(len(result["road_impacts"]), 12)
        self.assertIn("道路对象 12 个", result["warning"]["basis"])
        self.assertEqual(len(result["recommendations"]), 5)

    def test_unknown_geometry_and_bridge_elevation_are_not_safety_claims(self):
        roads = [road([(-20, 0), (20, 0)], "bridge", bridge_flag=True), {"road_id": "unknown", "ref": ""}]
        result = self.assess(roads, [cell()])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["limitations"])
        self.assertIn("桥隧路面高程未知", result["recommendations"][0]["basis"])

    def test_old_assessment_cache_is_invalidated(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cycle.json"
            run = {"forecast_id": "v001", "generated_at": "now"}
            path.write_text(json.dumps({"schema_version": forecast.FORECAST_SCHEMA_VERSION,
                                        "assessment_mode": "single", "forecast": run}))
            with patch.object(forecast, "forecast_cycle_path", return_value=path):
                self.assertIsNone(forecast.read_cached_emergency_cycle(run))
