from __future__ import annotations

import unittest

from domains.flood.runtime.hydrodynamic_grid import (
    gcj02_tile_bounds_wgs84,
    gcj02_to_wgs84,
    lonlat_to_tile,
    normalize_tile_crs,
    wgs84_to_gcj02,
)


class AMapProjectionTests(unittest.TestCase):
    def test_gcj02_round_trip_preserves_domain_coordinate(self):
        source = (111.35, 24.4)
        shifted = wgs84_to_gcj02(*source)
        restored = gcj02_to_wgs84(*shifted)

        self.assertGreater(abs(shifted[0] - source[0]), 0.001)
        self.assertGreater(abs(shifted[1] - source[1]), 0.001)
        self.assertAlmostEqual(source[0], restored[0], places=8)
        self.assertAlmostEqual(source[1], restored[1], places=8)

    def test_gcj02_tile_bounds_are_returned_in_wgs84(self):
        source = (111.35, 24.4)
        shifted = wgs84_to_gcj02(*source)
        x, y = lonlat_to_tile(*shifted, 15)
        min_lng, min_lat, max_lng, max_lat = gcj02_tile_bounds_wgs84(15, x, y)

        self.assertLessEqual(min_lng, source[0])
        self.assertGreaterEqual(max_lng, source[0])
        self.assertLessEqual(min_lat, source[1])
        self.assertGreaterEqual(max_lat, source[1])

    def test_tile_crs_aliases_are_normalized(self):
        self.assertEqual("gcj02", normalize_tile_crs("amap"))
        self.assertEqual("gcj02", normalize_tile_crs("GCJ-02"))
        self.assertEqual("wgs84", normalize_tile_crs("EPSG:4326"))


if __name__ == "__main__":
    unittest.main()
