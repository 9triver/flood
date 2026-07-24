from __future__ import annotations

import json
import unittest

from domains.flood.runtime.repository import object_library_path


class ReservoirObjectTest(unittest.TestCase):
    @staticmethod
    def _rows(object_type: str) -> list[dict]:
        return [
            json.loads(line)
            for line in object_library_path(object_type).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_longtan_reservoir_has_polygon_geometry_and_river_link(self):
        rows = self._rows("Reservoir")

        longtan = next(row for row in rows if row["reservoir_id"] == "longtan")

        self.assertEqual("龙潭水库", longtan["name"])
        self.assertEqual("shanhu", longtan["river_id"])
        self.assertEqual("Polygon", longtan["geometry_type"])
        self.assertEqual("Amap satellite imagery", longtan["external_geometry_source"])
        self.assertEqual("amap/style6/z18/2026-07-24", longtan["external_geometry_ref"])
        self.assertEqual("satellite_interpreted_water_extent", longtan["water_extent_type"])
        geometry = json.loads(longtan["geometry"])
        self.assertGreater(len(geometry["coordinates"][0]), 450)
        self.assertGreater(longtan["external_geometry_area_km2"], 0.55)
        self.assertLess(longtan["external_geometry_area_km2"], 0.57)

    def test_shanhu_river_connects_to_longtan_reservoir_boundary(self):
        river = next(row for row in self._rows("River") if row["river_id"] == "shanhu")
        longtan = next(row for row in self._rows("Reservoir") if row["reservoir_id"] == "longtan")
        river_coordinates = json.loads(river["geometry"])["coordinates"]
        reservoir_boundary = json.loads(longtan["geometry"])["coordinates"][0]

        self.assertIn(river_coordinates[0], reservoir_boundary)


if __name__ == "__main__":
    unittest.main()
