from __future__ import annotations

import json
import unittest

from domains.flood.runtime.common import PROJECT_DIR
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
        self.assertEqual("Amap standard map", longtan["external_geometry_source"])
        self.assertEqual("amap/style7/z18/2026-07-27", longtan["external_geometry_ref"])
        self.assertEqual("cartographic_water_extent", longtan["water_extent_type"])
        geometry = json.loads(longtan["geometry"])
        self.assertGreater(len(geometry["coordinates"][0]), 150)
        self.assertGreater(longtan["external_geometry_area_km2"], 1.23)
        self.assertLess(longtan["external_geometry_area_km2"], 1.25)
        self.assertLess(min(point[1] for point in geometry["coordinates"][0]), 24.302)

    def test_shanhu_river_connects_to_longtan_reservoir_boundary(self):
        river = next(row for row in self._rows("River") if row["river_id"] == "shanhu")
        longtan = next(row for row in self._rows("Reservoir") if row["reservoir_id"] == "longtan")
        river_coordinates = json.loads(river["geometry"])["coordinates"]
        reservoir_boundary = json.loads(longtan["geometry"])["coordinates"][0]

        self.assertIn(river_coordinates[0], reservoir_boundary)

        source_path = PROJECT_DIR / longtan["data_path"]
        source = json.loads(source_path.read_text(encoding="utf-8"))
        connection = next(
            feature["geometry"]["coordinates"]
            for feature in source["features"]
            if feature["properties"].get("role") == "river_connection"
        )
        self.assertGreater(len(connection), 5)
        self.assertEqual(connection, river_coordinates[:len(connection)])


if __name__ == "__main__":
    unittest.main()
