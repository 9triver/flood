from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from domains.flood.runtime import evacuation_timing


class FakeResolver:
    def __init__(self):
        self.transfer = {
            "evacuation_unit_id": "40",
            "name": "新民村转移单元",
            "source_name": "新民村",
            "population": 391,
            "flood_arrival_window": "0-12",
            "longitude": 111.00000,
            "latitude": 24.00000,
        }
        self.route = {
            "evacuation_route_id": "40",
            "name": "新民村转移路线",
            "route_type": "transfer",
            "origin_unit_id": "40",
            "destination_site_id": "shelter_232",
            "duration_s": 600,
            "geometry": json.dumps({
                "type": "LineString",
                "coordinates": [[111.00000, 24.00000], [111.00010, 24.00000]],
            }),
        }
        self.place = {
            "evacuation_site_id": "shelter_232",
            "name": "新民村安置点",
            "site_type": "shelter",
            "longitude": 111.00010,
            "latitude": 24.00000,
        }
        self.forecast_run = {
            "forecast_id": "v001",
            "forecast_time": "2025-01-01T00:00:00+08:00",
            "valid_from": "2025-01-01T00:00:00+08:00",
            "valid_to": "2025-01-02T00:00:00+08:00",
            "boundary_flow": json.dumps({
                "window_start": "2025-01-01T00:00:00+08:00",
                "observed_through": "2025-01-01T00:30:00+08:00",
            }),
        }

    def query_by_id(self, object_type, object_id):
        rows = {
            "EvacuationUnit": self.transfer,
            "EvacuationRoute": self.route,
            "EvacuationSite": self.place,
        }
        row = rows.get(object_type)
        id_fields = {
            "EvacuationUnit": "evacuation_unit_id",
            "EvacuationRoute": "evacuation_route_id",
            "EvacuationSite": "evacuation_site_id",
        }
        if row and str(row[id_fields[object_type]]) == str(object_id):
            return dict(row)
        return None

    def query(self, object_type, filters=None, limit=None, **_kwargs):
        rows = {
            "EvacuationUnit": [self.transfer],
            "EvacuationRoute": [self.route],
            "EvacuationSite": [self.place],
            "FloodForecast": [self.forecast_run],
        }.get(object_type, [])
        rows = [row for row in rows if all(row.get(key) == value for key, value in (filters or {}).items())]
        return [dict(row) for row in rows[:limit]] if limit else [dict(row) for row in rows]


class EvacuationTimingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.mesh_path = self.root / "mesh.sqlite"
        with closing(sqlite3.connect(self.mesh_path)) as conn, conn:
            conn.execute(
                "create table cells ("
                "cell_id integer primary key, min_lon real, min_lat real, "
                "max_lon real, max_lat real, lon1 real, lat1 real, "
                "lon2 real, lat2 real, lon3 real, lat3 real)"
            )
            conn.execute(
                "insert into cells values (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    110.99980, 23.99980, 111.00030, 24.00030,
                    110.99980, 23.99980,
                    111.00005, 24.00030,
                    111.00030, 23.99980,
                ),
            )
        self.series_path = self.root / "depth_series.npy"
        self.time_steps = [0.5, 1.0, 1.5, 24.0]
        self.resolver = FakeResolver()

    def test_deadline_preserves_selected_routes_water_depth_threshold(self):
        self.resolver.route["profile"] = "foot"
        self.resolver.route["blocked_depth_m"] = 0.15
        result = self.analyze([0.0, 0.0, 0.2, 0.2])
        self.assertEqual(result["parameters"]["blocked_depth_m"], 0.15)
        self.assertEqual(result["deadline"]["first_unsafe_time_h"], 1.5)

    def tearDown(self):
        self.tempdir.cleanup()

    def analyze(self, depths, **kwargs):
        np.save(self.series_path, np.asarray(depths, dtype=np.float32).reshape(-1, 1))
        with patch.object(
            evacuation_timing, "MESH_DB_PATH", self.mesh_path,
        ), patch.object(
            evacuation_timing, "forecast_series_path", return_value=self.series_path,
        ), patch.object(
            evacuation_timing, "forecast_time_steps", return_value=self.time_steps,
        ):
            return evacuation_timing.analyze_latest_evacuation_time(
                self.resolver,
                evacuation_unit_name="新民村",
                **kwargs,
            )

    def test_returns_last_confirmed_safe_slice_before_route_is_blocked(self):
        result = self.analyze([0.0, 0.2, 0.35, 0.5])

        self.assertEqual("completed", result["status"])
        self.assertEqual("route_becomes_unsafe", result["deadline_status"])
        deadline = result["deadline"]
        self.assertEqual(1.5, deadline["first_unsafe_time_h"])
        self.assertEqual(1.0, deadline["latest_safe_completion_time_h"])
        self.assertEqual(0.833, deadline["latest_departure_time_h"])
        self.assertEqual("2025-01-01T01:30:00+08:00", deadline["first_unsafe_at"])
        self.assertEqual("2025-01-01T01:00:00+08:00", deadline["latest_safe_completion_at"])
        self.assertEqual(0.5, deadline["remaining_to_completion_h"])
        self.assertIn("route", deadline["first_unsafe_components"])
        self.assertEqual(
            "2025-01-01T00:00:00+08:00",
            result["forecast_window"]["valid_from"],
        )
        self.assertEqual(
            "2025-01-01T00:30:00+08:00",
            result["evidence"]["depth_timeline"][0]["valid_at"],
        )

    def test_uses_confirmed_clearance_duration_and_safety_buffer(self):
        result = self.analyze(
            [0.0, 0.2, 0.35, 0.5],
            clearance_duration_min=20,
            safety_buffer_min=10,
        )

        self.assertEqual(0.5, result["deadline"]["latest_departure_time_h"])
        self.assertEqual(
            "user_provided_clearance_duration",
            result["parameters"]["clearance_duration_source"],
        )
        self.assertEqual([], result["limitations"])

    def test_does_not_turn_forecast_horizon_into_a_deadline(self):
        result = self.analyze([0.0, 0.1, 0.2, 0.25])

        self.assertEqual("safe_through_horizon", result["deadline_status"])
        deadline = result["deadline"]
        self.assertEqual(24.0, deadline["last_confirmed_safe_time_h"])
        self.assertIsNone(deadline["latest_safe_completion_time_h"])
        self.assertIsNone(deadline["latest_departure_time_h"])
        self.assertIn("没有形成转移截止时间", deadline["message"])

    def test_requires_a_unique_transfer(self):
        result = evacuation_timing.analyze_latest_evacuation_time(
            self.resolver,
        )

        self.assertEqual("evacuation_unit_required", result["status"])

    def test_rejects_an_incomplete_24_hour_series(self):
        self.time_steps = [0.5, 1.0, 1.5, 2.0]

        result = self.analyze([0.0, 0.1, 0.2, 0.25])

        self.assertEqual("incomplete_forecast_horizon", result["status"])
        self.assertEqual(2.0, result["available_horizon_h"])

    def test_explicit_prediction_uses_its_own_metadata_and_series(self):
        original_query = self.resolver.query
        newer = {**self.resolver.forecast_run, "forecast_id": "v002", "valid_from": "2025-01-01T06:00:00+08:00"}
        def query(kind, filters=None, **kwargs):
            if kind == "FloodForecast" and not filters:
                return [newer]
            return original_query(kind, filters, **kwargs)
        self.resolver.query = query
        result = self.analyze([0, 0.2, 0.35, 0.5], forecast_id="v001")
        self.assertEqual(result["forecast_id"], "v001")
        self.assertEqual(result["deadline"]["first_unsafe_at"], "2025-01-01T01:30:00+08:00")
        self.assertEqual(self.analyze([0, 0, 0, 0], forecast_id="missing")["status"], "forecast_unavailable")

    def test_latest_is_resolved_before_series_reads(self):
        original = evacuation_timing.forecast_series_path
        with patch.object(evacuation_timing, "forecast_series_path", wraps=original) as read:
            # The real helper is replaced only for the path; record the version.
            read.side_effect = lambda ident: self.series_path
            np.save(self.series_path, np.array([[0], [0.2], [0.4], [0.5]]))
            with patch.object(evacuation_timing, "forecast_time_steps", return_value=self.time_steps), patch.object(evacuation_timing, "MESH_DB_PATH", self.mesh_path):
                result = evacuation_timing.analyze_latest_evacuation_time(self.resolver, evacuation_unit_id="40")
        self.assertEqual(read.call_args.args, ("v001",))
        self.assertEqual(result["forecast_id"], "v001")

    def test_large_triangle_intersection_is_detected_far_from_centroid(self):
        with closing(sqlite3.connect(self.mesh_path)) as conn, conn:
            conn.execute("update cells set min_lon=110.99, max_lon=111.01, min_lat=23.99, max_lat=24.025, "
                         "lon1=110.99, lat1=23.99, lon2=111.01, lat2=23.99, lon3=111.0, lat3=24.025")
        result = self.analyze([0, 0, 0.4, 0.5])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["deadline"]["first_unsafe_time_h"], 1.5)
        self.assertEqual(result["parameters"]["spatial_method"], "full_geometry_polygon_intersection")

    def test_wrong_origin_route_is_rejected(self):
        self.resolver.route["origin_unit_id"] = "other-village"
        result = self.analyze([0, 0, 0, 0], evacuation_route_id="40")
        self.assertEqual(result["status"], "route_origin_mismatch")

    def test_invalid_route_geometry_cannot_form_a_window(self):
        for geometry in ('[]', {'type': 'LineString', 'coordinates': [[111, 24], [111, 24]]},
                         {'type': 'Polygon', 'coordinates': [[[111, 24], [111.001, 24], [111, 24.001], [111, 24]]]}):
            with self.subTest(geometry=geometry):
                self.resolver.route['geometry'] = geometry
                self.assertEqual(self.analyze([0, 0, 0, 0])['status'], 'invalid_route_geometry')

    def test_missing_destination_coverage_preserves_risk_but_not_safe_deadline(self):
        self.resolver.place["longitude"] = 112
        result = self.analyze([0, 0, 0.4, 0.5])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["deadline_status"], "incomplete_coverage")
        self.assertEqual(result["deadline"]["first_unsafe_time_h"], 1.5)
        self.assertIsNone(result["deadline"]["latest_departure_at"])
        self.assertIsNone(result["evidence"]["depth_timeline"][0]["component_depths_m"]["destination"])

    def test_uncovered_route_length_cannot_be_called_safe(self):
        self.resolver.route["geometry"] = {"type": "LineString", "coordinates": [[111, 24], [112, 24], [111.0001, 24]]}
        result = self.analyze([0, 0, 0, 0])
        self.assertEqual(result["status"], "partial")
        self.assertGreater(result["coverage"]["route"]["uncovered_length_m"], 0)
        self.assertIsNone(result["deadline"]["last_confirmed_safe_at"])

    def test_invalid_matched_depth_does_not_become_safe(self):
        self.assertEqual(self.analyze([0, float('nan'), 0, 0])["status"], "invalid_forecast_series")


if __name__ == "__main__":
    unittest.main()
