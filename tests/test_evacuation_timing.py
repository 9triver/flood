from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from domains.flood.runtime import evacuation_timing


class FakeResolver:
    def __init__(self):
        self.transfer = {
            "transfer_id": "40",
            "name": "新民村",
            "population": 391,
            "arrive_time_window": "0-12",
            "route_id": "40",
            "place_id": "shelter_232",
            "longitude": 111.00000,
            "latitude": 24.00000,
        }
        self.route = {
            "route_id": "40",
            "name": "新民村",
            "route_type": "transfer",
            "place_id": "shelter_232",
            "duration_s": 600,
            "geometry": json.dumps({
                "type": "LineString",
                "coordinates": [[111.00000, 24.00000], [111.00010, 24.00000]],
            }),
        }
        self.place = {
            "place_id": "shelter_232",
            "name": "新民村安置点",
            "place_type": "shelter",
            "longitude": 111.00010,
            "latitude": 24.00000,
        }
        self.forecast_run = {
            "forecast_id": "forecast_latest",
            "boundary_flow": json.dumps({
                "window_start": "2025-01-01T00:00:00+08:00",
                "observed_through": "2025-01-01T00:30:00+08:00",
            }),
        }

    def query_by_id(self, object_type, object_id):
        rows = {
            "Transfer": self.transfer,
            "Route": self.route,
            "Place": self.place,
        }
        row = rows.get(object_type)
        id_fields = {
            "Transfer": "transfer_id",
            "Route": "route_id",
            "Place": "place_id",
        }
        if row and str(row[id_fields[object_type]]) == str(object_id):
            return dict(row)
        return None

    def query(self, object_type, filters=None, limit=None, **_kwargs):
        rows = {
            "Transfer": [self.transfer],
            "Route": [self.route],
            "Place": [self.place],
            "ForecastRun": [self.forecast_run],
        }.get(object_type, [])
        return [dict(row) for row in rows[:limit]] if limit else [dict(row) for row in rows]


class EvacuationTimingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.mesh_path = self.root / "mesh.sqlite"
        with sqlite3.connect(self.mesh_path) as conn:
            conn.execute(
                "create table cells ("
                "cell_id integer primary key, min_lon real, min_lat real, "
                "max_lon real, max_lat real, lon1 real, lat1 real, "
                "lon2 real, lat2 real, lon3 real, lat3 real)"
            )
            conn.execute(
                "insert into cells values (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    110.99995, 23.99995, 111.00015, 24.00005,
                    111.00000, 23.99995,
                    111.00010, 24.00005,
                    111.00010, 23.99995,
                ),
            )
        self.series_path = self.root / "depth_series.npy"
        self.time_steps = [0.5, 1.0, 1.5, 24.0]
        self.resolver = FakeResolver()

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
                transfer_name="新民村",
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

        self.assertEqual("transfer_required", result["status"])

    def test_rejects_an_incomplete_24_hour_series(self):
        self.time_steps = [0.5, 1.0, 1.5, 2.0]

        result = self.analyze([0.0, 0.1, 0.2, 0.25])

        self.assertEqual("incomplete_forecast_horizon", result["status"])
        self.assertEqual(2.0, result["available_horizon_h"])


if __name__ == "__main__":
    unittest.main()
