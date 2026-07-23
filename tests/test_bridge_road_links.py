from __future__ import annotations

import unittest

from domains.flood.runtime.repository import read_object_library


class BridgeRoadLinkDataTest(unittest.TestCase):
    def test_bridge_inventory_is_deduplicated_with_source_provenance(self):
        bridges = read_object_library("Bridge")
        coordinates = {
            (round(float(row["longitude"]), 7), round(float(row["latitude"]), 7))
            for row in bridges
        }

        self.assertEqual(len(bridges), 22)
        self.assertEqual(len(coordinates), len(bridges))
        self.assertEqual(
            sum(int(row.get("source_record_count") or 0) for row in bridges),
            44,
        )

    def test_accepted_links_have_referential_and_osm_evidence(self):
        bridges = {
            row["bridge_id"]: row
            for row in read_object_library("Bridge")
        }
        roads = {
            row["road_id"]: row
            for row in read_object_library("Road")
        }
        links = read_object_library("BridgeRoadLink")

        self.assertEqual(len(links), 3)
        for link in links:
            bridge = bridges[link["bridge_id"]]
            road = roads[link["road_id"]]
            self.assertEqual(link["validation_status"], "accepted")
            self.assertLessEqual(float(link["distance_m"]), 20.0)
            self.assertGreaterEqual(float(link["confidence"]), 0.8)
            self.assertTrue(road["bridge_flag"])
            self.assertEqual(bridge["osm_ref"], road["osm_ref"])
            self.assertEqual(link["bridge_osm_ref"], road["osm_ref"])
            self.assertEqual(link["road_osm_ref"], road["osm_ref"])


if __name__ == "__main__":
    unittest.main()
