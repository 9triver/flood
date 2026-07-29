from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import hydrodynamic_grid


class HydrodynamicGridCacheTest(unittest.TestCase):
    def setUp(self):
        with hydrodynamic_grid._DEPTH_CACHE_LOCK:
            hydrodynamic_grid._DEPTH_CACHE.clear()
            hydrodynamic_grid._DEPTH_LOADS.clear()
        with hydrodynamic_grid._TILE_CACHE_LOCK:
            hydrodynamic_grid._TILE_CACHE.clear()

    def tearDown(self):
        with hydrodynamic_grid._DEPTH_CACHE_LOCK:
            hydrodynamic_grid._DEPTH_CACHE.clear()
            hydrodynamic_grid._DEPTH_LOADS.clear()
        with hydrodynamic_grid._TILE_CACHE_LOCK:
            hydrodynamic_grid._TILE_CACHE.clear()

    def test_max_depth_cache_keeps_only_positive_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max_depth.csv"
            path.write_text(
                "cell_id,max_depth\n1,0\n2,0.4\n3,0.0\n4,1.2\n",
                encoding="utf-8",
            )

            entry = hydrodynamic_grid.load_forecast_depth_entry(
                path,
                hydrodynamic_grid.file_stat_key(path),
            )

        self.assertEqual({2: 0.4, 4: 1.2}, entry["depths"])
        self.assertEqual(4, entry["depth_count"])
        self.assertEqual(2, entry["flooded_count"])
        self.assertEqual(1.2, entry["max_depth_m"])

    def test_concurrent_max_depth_requests_load_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max_depth.csv"
            path.write_text("cell_id,max_depth\n1,0.4\n", encoding="utf-8")
            call_count = 0
            count_lock = threading.Lock()

            def load_once(load_path, stat_key):
                nonlocal call_count
                with count_lock:
                    call_count += 1
                time.sleep(0.05)
                return {
                    "stat_key": stat_key,
                    "depths": {1: 0.4},
                    "depth_count": 1,
                    "flooded_count": 1,
                    "max_depth_m": 0.4,
                    "time_h": None,
                    "time_index": None,
                }

            with patch.object(
                hydrodynamic_grid, "forecast_depth_path", return_value=path,
            ), patch.object(
                hydrodynamic_grid,
                "load_forecast_depth_entry",
                side_effect=load_once,
            ):
                results = self._run_concurrently(
                    lambda: hydrodynamic_grid.forecast_depth_entry("latest"),
                )

        self.assertEqual(1, call_count)
        self.assertTrue(all(result is results[0] for result in results))

    def test_concurrent_time_depth_requests_load_once(self):
        with tempfile.TemporaryDirectory() as directory:
            series_path = Path(directory) / "depth_series.npy"
            steps_path = Path(directory) / "time_steps.json"
            series_path.write_bytes(b"series")
            steps_path.write_text('{"time_steps_h":[0,1]}', encoding="utf-8")
            call_count = 0
            count_lock = threading.Lock()

            def load_once(load_path, steps, requested_time_h, stat_key):
                nonlocal call_count
                with count_lock:
                    call_count += 1
                time.sleep(0.05)
                return {
                    "stat_key": stat_key,
                    "depths": {2: 0.6},
                    "depth_count": 2,
                    "flooded_count": 1,
                    "max_depth_m": 0.6,
                    "time_h": 1.0,
                    "time_index": 1,
                }

            with patch.object(
                hydrodynamic_grid,
                "forecast_series_path",
                return_value=series_path,
            ), patch.object(
                hydrodynamic_grid,
                "forecast_time_steps_path",
                return_value=steps_path,
            ), patch.object(
                hydrodynamic_grid,
                "forecast_time_steps",
                return_value=[0.0, 1.0],
            ), patch.object(
                hydrodynamic_grid,
                "load_forecast_time_depth_entry",
                side_effect=load_once,
            ):
                results = self._run_concurrently(
                    lambda: hydrodynamic_grid.forecast_time_depth_entry(
                        "latest", 1.0,
                    ),
                )

        self.assertEqual(1, call_count)
        self.assertTrue(all(result is results[0] for result in results))

    def test_forecast_stats_exposes_rainfall_series_from_boundary_flow_metadata(self):
        rainfall_series = [{
            "time_h": 0,
            "valid_time": "2026-07-03T08:00:00+08:00",
            "rainfall_mm": 6.25,
        }]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            depth_path = root / "max_depth.csv"
            depth_path.write_text(
                "cell_id,max_depth\n1,0.4\n",
                encoding="utf-8",
            )
            missing_series_path = root / "depth_series.npy"
            missing_steps_path = root / "time_steps.json"
            metadata = {
                "forecast_id": "v001",
                "boundary_flow": json.dumps({
                    "rainfall_series": rainfall_series,
                }),
            }
            depth_entry = {
                "depth_count": 1,
                "flooded_count": 1,
                "max_depth_m": 0.4,
            }

            with patch.object(
                hydrodynamic_grid, "PROJECT_DIR", root,
            ), patch.object(
                hydrodynamic_grid, "forecast_depth_path", return_value=depth_path,
            ), patch.object(
                hydrodynamic_grid,
                "forecast_series_path",
                return_value=missing_series_path,
            ), patch.object(
                hydrodynamic_grid,
                "forecast_time_steps_path",
                return_value=missing_steps_path,
            ), patch.object(
                hydrodynamic_grid, "forecast_time_steps", return_value=[],
            ), patch.object(
                hydrodynamic_grid, "forecast_metadata", return_value=metadata,
            ), patch.object(
                hydrodynamic_grid,
                "forecast_depth_entry",
                return_value=depth_entry,
            ):
                stats = hydrodynamic_grid.forecast_stats("latest")

        self.assertEqual(rainfall_series, stats["rainfall_series"])

    def test_wet_tile_queries_only_forecast_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "mesh.sqlite"
            lon, lat = 111.25, 24.4
            z = 13
            x, y = hydrodynamic_grid.lonlat_to_tile(lon, lat, z)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    create table cells(
                        cell_id integer primary key,
                        min_lon real, min_lat real, max_lon real, max_lat real,
                        lon1 real, lat1 real, lon2 real, lat2 real, lon3 real, lat3 real
                    )
                    """
                )
                conn.executemany(
                    "insert into cells values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (1, lon - 0.001, lat - 0.001, lon + 0.001, lat + 0.001,
                         lon - 0.001, lat - 0.001, lon + 0.001, lat - 0.001, lon, lat + 0.001),
                        (2, 120.0, 30.0, 120.01, 30.01,
                         120.0, 30.0, 120.01, 30.0, 120.0, 30.01),
                    ],
                )
            store = hydrodynamic_grid.HydrodynamicMeshStore(db_path)
            depth_entry = {
                "stat_key": (1, 1),
                "depths": {1: 0.4, 2: 0.8},
                "time_h": 1.0,
                "time_index": 1,
            }
            with patch.object(store, "ensure_ready"), patch.object(
                hydrodynamic_grid,
                "forecast_depth_entry",
                return_value=depth_entry,
            ), patch.object(store, "_tile_rows") as full_tile_rows:
                tile = store.tile(z, x, y, wet_only=True, time_h=1.0)

        full_tile_rows.assert_not_called()
        self.assertEqual([1], [cell[0] for cell in tile["cells"]])

    @staticmethod
    def _run_concurrently(call):
        worker_count = 8
        barrier = threading.Barrier(worker_count)

        def invoke():
            barrier.wait()
            return call()

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            return list(pool.map(lambda _: invoke(), range(worker_count)))


if __name__ == "__main__":
    unittest.main()
