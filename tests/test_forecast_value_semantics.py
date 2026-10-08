"""Model outputs, estimates and missing values must remain distinguishable."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import forecast_query
from domains.flood.runtime.impact_analysis import make_impact
from tests.test_linear_inundation import analyze, cell, road


class ForecastValueSemanticsTests(unittest.TestCase):
    def test_depth_is_not_water_level_and_slice_is_not_arrival(self):
        with tempfile.TemporaryDirectory() as directory:
            mesh = Path(directory) / "mesh.sqlite"
            with closing(sqlite3.connect(mesh)) as conn, conn:
                conn.execute("create table cells (cell_id int, lon1 real, lat1 real, lon2 real, lat2 real, lon3 real, lat3 real)")
                conn.execute("insert into cells values (1, 111.35, 24.35, 111.37, 24.35, 111.36, 24.37)")
            with patch.object(forecast_query, "MESH_DB_PATH", mesh):
                for hour in (None, 6.0):
                    with self.subTest(hour=hour):
                        value = forecast_query.forecast_cells_from_hydrodynamic_mesh(
                            {1: 0.8}, "2026-07-03T08:00:00+08:00", hour, "v001",
                        )[0]
                        self.assertEqual(value["depth_m"], 0.8)
                        self.assertEqual(value["depth_source"], "cnn_prediction")
                        self.assertEqual(value["time_h"], hour)
                        self.assertEqual(value["lead_time_h"], hour)
                        self.assertEqual(value["view"], "envelope" if hour is None else "time_slice")
                        for field in ("arrival_time_h", "recession_time_h", "ground_elevation_m",
                                      "water_level_m", "distance_to_river_m", "river_along_ratio"):
                            self.assertIsNone(value[field], field)
                        self.assertEqual(value["velocity_source"], "depth_estimate")
                        self.assertGreater(value["velocity_mps"], 0)
                        self.assertEqual(value["risk_basis"], "depth_and_estimated_velocity")
                        self.assertEqual(value["risk_level"], "medium")

    def test_estimate_provenance_survives_road_and_route_aggregation(self):
        wet = {**cell(), "depth_source": "cnn_prediction", "velocity_source": "depth_estimate",
               "risk_basis": "depth_and_estimated_velocity"}
        result = analyze([road([(-20, 0), (20, 0)])], [wet])
        for impact in [*result["impacts"], *result["road_route_impacts"]]:
            self.assertEqual(impact["velocity_source"], "depth_estimate")
            self.assertEqual(impact["risk_basis"], "depth_and_estimated_velocity")
            self.assertEqual(impact["depth_source"], "cnn_prediction")

    def test_missing_velocity_does_not_become_zero(self):
        impact = make_impact("EvacuationSite", {"evacuation_site_id": "a"}, "evacuation_site_id",
                             {"depth_m": 0.8, "velocity_mps": None}, "nearest_cell", (111.36, 24.36))
        self.assertIsNone(impact["velocity_mps"])
        self.assertEqual(impact["velocity_source"], "unavailable")
