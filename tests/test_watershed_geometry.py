from __future__ import annotations

import unittest

from domains.flood.build_objects import _smooth_polygon_geometry


def signed_area(ring):
    return sum(
        ring[index][0] * ring[index + 1][1]
        - ring[index + 1][0] * ring[index][1]
        for index in range(len(ring) - 1)
    ) / 2


class WatershedGeometryTest(unittest.TestCase):
    def test_smoothing_closes_ring_and_preserves_shape_orientation(self):
        ring = [
            [111.0000, 24.0000],
            [111.0010, 24.0000],
            [111.0010, 24.0008],
            [111.0020, 24.0008],
            [111.0020, 24.0020],
            [111.0000, 24.0020],
            [111.0000, 24.0000],
        ]
        result = _smooth_polygon_geometry(
            {"type": "Polygon", "coordinates": [ring]},
            tolerance_m=45,
            ratio=0.18,
            iterations=2,
        )
        smoothed = result["coordinates"][0]

        self.assertEqual(smoothed[0], smoothed[-1])
        self.assertNotEqual(ring, smoothed)
        self.assertGreater(len(smoothed), 4)
        self.assertGreater(signed_area(ring) * signed_area(smoothed), 0)
        self.assertLess(
            abs(signed_area(smoothed) - signed_area(ring))
            / abs(signed_area(ring)),
            0.1,
        )

    def test_non_polygon_geometry_is_left_unchanged(self):
        geometry = {"type": "LineString", "coordinates": [[1, 2], [3, 4]]}

        self.assertIs(
            geometry,
            _smooth_polygon_geometry(
                geometry,
                tolerance_m=75,
                ratio=0.18,
                iterations=2,
            ),
        )

    def test_triangle_ring_remains_closed(self):
        result = _smooth_polygon_geometry(
            {
                "type": "Polygon",
                "coordinates": [[
                    [111.0, 24.0],
                    [111.1, 24.0],
                    [111.0, 24.1],
                    [111.0, 24.0],
                ]],
            },
            tolerance_m=75,
            ratio=0.18,
            iterations=2,
        )

        ring = result["coordinates"][0]
        self.assertEqual(4, len(ring))
        self.assertEqual(ring[0], ring[-1])


if __name__ == "__main__":
    unittest.main()
