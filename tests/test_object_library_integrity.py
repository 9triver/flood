from __future__ import annotations

from collections import Counter
import json
import unittest

import yaml

from domains.flood.build_objects import (
    FloodObjectBuilder,
    _strip_station_type_suffix,
)
from domains.flood.runtime.common import OBJECT_LIBRARY_FILES, OBJECTS_DIR, PROJECT_DIR
from domains.flood.runtime.repository import read_object_library


class FloodObjectLibraryIntegrityTest(unittest.TestCase):
    def test_station_type_suffix_is_removed_from_display_name(self):
        self.assertEqual("水寨", _strip_station_type_suffix("水寨(山洪)"))
        self.assertEqual("水寨", _strip_station_type_suffix("水寨（山洪）"))

    def test_station_library_matches_source_inventory(self):
        stations = read_object_library("Station")

        self.assertEqual(32, len(stations))
        self.assertEqual(32, len({row["station_id"] for row in stations}))
        self.assertEqual(
            {
                "flash_flood": 13,
                "meteorological": 12,
                "hydrological": 6,
                "reservoir": 1,
            },
            dict(Counter(row["station_type"] for row in stations)),
        )
        self.assertTrue(all(
            row["station_id"] == row["station_code"]
            and row["river_id"] == "shanhu"
            and row["geometry_type"] == "Point"
            and "(" not in row["name"]
            and "（" not in row["name"]
            for row in stations
        ))
        for row in stations:
            coordinates = json.loads(row["geometry"])["coordinates"]
            self.assertEqual(
                [row["longitude"], row["latitude"]], coordinates,
            )
        by_id = {row["station_id"]: row for row in stations}
        self.assertEqual("水寨", by_id["808J017Y"]["name"])
        self.assertEqual("水寨(山洪)", by_id["808J017Y"]["source_name"])
        self.assertEqual(
            "normalized_source_name", by_id["808J017Y"]["name_source"],
        )
        self.assertEqual("气象", by_id["N4593"]["station_type_name"])
        self.assertEqual("凤翔", by_id["808J0210"]["name"])
        longtan = by_id["HP0014511220000128"]
        self.assertEqual("龙潭水库", longtan["name"])
        self.assertEqual("reservoir", longtan["station_type"])
        self.assertEqual("longtan", longtan["reservoir_id"])
        self.assertEqual("exact_geometry_intersection", longtan["coordinate_quality"])
        self.assertEqual(
            [111.36935270331932, 24.32545936202293],
            json.loads(longtan["geometry"])["coordinates"],
        )
        self.assertEqual(stations, FloodObjectBuilder().build("Station"))

    def test_reservoir_library_contains_only_longtan(self):
        reservoirs = read_object_library("Reservoir")

        self.assertEqual(1, len(reservoirs))
        self.assertEqual("longtan", reservoirs[0]["reservoir_id"])
        self.assertEqual("龙潭水库", reservoirs[0]["name"])
        self.assertEqual("shanhu", reservoirs[0]["river_id"])

    def test_duplicate_sluice_source_rows_are_merged(self):
        sluices = read_object_library("Sluice")

        self.assertEqual(1, len(sluices))
        self.assertEqual("451122000004", sluices[0]["sluice_id"])
        self.assertEqual(3, sluices[0]["source_record_count"])
        self.assertEqual(
            ["451122000004_1", "451122000004_2", "451122000004_3"],
            sluices[0]["source_record_ids"].split(","),
        )

    def test_duplicate_hydraulic_structure_geometries_are_merged(self):
        structures = read_object_library("HydraulicStructure")
        geometry_keys = {
            (row["structure_type"], row["geometry"])
            for row in structures
        }

        self.assertEqual(20, len(structures))
        self.assertEqual(len(structures), len(geometry_keys))
        self.assertEqual(
            9,
            len([
                row for row in structures
                if row["structure_type"] == "spillway"
            ]),
        )
        self.assertTrue(all(
            row["source_record_count"] == 3
            for row in structures
            if row["structure_type"] == "spillway"
        ))

    def test_static_objects_have_deterministic_ownership(self):
        town_ids = {row["town_id"] for row in read_object_library("Town")}
        county_ids = {row["county_id"] for row in read_object_library("County")}
        facilities = read_object_library("Facility")
        roads = read_object_library("Road")
        bridges = read_object_library("Bridge")

        self.assertEqual(106, len(facilities))
        self.assertTrue(all(row.get("town_id") in town_ids for row in facilities))
        self.assertTrue(all(row.get("town_name") for row in facilities))
        self.assertEqual(423, len(roads))
        self.assertTrue(all(row.get("county_id") in county_ids for row in roads))
        self.assertFalse(any(
            row.get("geometry_source") == "osm_bridge_way"
            for row in roads
        ))
        self.assertEqual(roads, FloodObjectBuilder().build("Road"))
        self.assertEqual(22, len(bridges))
        self.assertTrue(all(row.get("river_id") == "shanhu" for row in bridges))
        coordinates = {
            (round(float(row["longitude"]), 7), round(float(row["latitude"]), 7))
            for row in bridges
        }
        self.assertEqual(22, len(coordinates))
        self.assertEqual(
            44,
            sum(int(row.get("source_record_count") or 0) for row in bridges),
        )

    def test_danger_area_library_only_contains_static_fields(self):
        dynamic_fields = {
            "target_type",
            "target_id",
            "max_depth_m",
            "max_velocity_mps",
            "first_arrival_time_h",
            "affected_length_m",
            "affected_population",
        }
        danger_areas = read_object_library("DangerArea")

        self.assertEqual(24, len(danger_areas))
        self.assertTrue(all(
            row["danger_area_type"] == "flood_danger_area"
            for row in danger_areas
        ))
        self.assertTrue(all(dynamic_fields.isdisjoint(row) for row in danger_areas))

    def test_evacuation_links_are_owned_by_routes(self):
        units = read_object_library("EvacuationUnit")
        routes = read_object_library("EvacuationRoute")
        sites = read_object_library("EvacuationSite")
        unit_ids = {row["evacuation_unit_id"] for row in units}
        site_ids = {row["evacuation_site_id"] for row in sites}

        self.assertTrue(units)
        self.assertTrue(routes)
        self.assertTrue(sites)
        self.assertTrue(all(
            {"route_id", "place_id", "transfer_id"}.isdisjoint(row)
            for row in [*units, *routes, *sites]
        ))
        self.assertTrue(all(row["origin_unit_id"] in unit_ids for row in routes))
        self.assertTrue(all(
            row["destination_site_id"] in site_ids for row in routes
        ))
        self.assertTrue(all(row["length_m"] > 100 for row in routes))

    def test_normalized_names_preserve_source_name(self):
        named_types = (
            "HydrodynamicBoundary",
            "Sluice",
            "Bridge",
            "Facility",
            "HydraulicStructure",
            "Road",
            "EvacuationSite",
            "EvacuationUnit",
            "EvacuationRoute",
            "DangerArea",
        )
        for object_type in named_types:
            with self.subTest(object_type=object_type):
                rows = read_object_library(object_type)
                self.assertTrue(rows)
                self.assertTrue(all(row.get("name") for row in rows))
                self.assertTrue(all(row.get("name_source") for row in rows))
                self.assertTrue(all("source_name" in row for row in rows))

    def test_hydrology_is_not_an_object_type(self):
        ontology = yaml.safe_load(
            (PROJECT_DIR / "domains/flood/ontology.yaml").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (OBJECTS_DIR / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertNotIn("Hydrology", ontology["objects"])
        self.assertNotIn("Hydrology", OBJECT_LIBRARY_FILES)
        self.assertNotIn("Hydrology", manifest["object_types"])
        self.assertFalse((OBJECTS_DIR / "hydrology.jsonl").exists())

    def test_bridge_road_link_is_not_an_object_type(self):
        ontology = yaml.safe_load(
            (PROJECT_DIR / "domains/flood/ontology.yaml").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (OBJECTS_DIR / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertNotIn("BridgeRoadLink", ontology["objects"])
        self.assertNotIn("BridgeRoadLink", OBJECT_LIBRARY_FILES)
        self.assertNotIn("BridgeRoadLink", manifest["object_types"])
        self.assertFalse((OBJECTS_DIR / "bridge_road_link.jsonl").exists())

    def test_station_replaces_legacy_hydrometeorological_station_type(self):
        ontology = yaml.safe_load(
            (PROJECT_DIR / "domains/flood/ontology.yaml").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (OBJECTS_DIR / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertIn("Station", ontology["objects"])
        self.assertNotIn("HydrometeorologicalStation", ontology["objects"])
        self.assertEqual("station.jsonl", OBJECT_LIBRARY_FILES["Station"])
        self.assertNotIn("HydrometeorologicalStation", OBJECT_LIBRARY_FILES)
        self.assertEqual(32, manifest["object_types"]["Station"]["count"])
        self.assertNotIn(
            "HydrometeorologicalStation", manifest["object_types"],
        )
        self.assertFalse(
            (OBJECTS_DIR / "hydrometeorological_station.jsonl").exists(),
        )


if __name__ == "__main__":
    unittest.main()
