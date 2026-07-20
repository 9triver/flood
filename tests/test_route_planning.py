from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import route_planning
from scripts.graphhopper import build_osm_xml


class EmptyResolver:
    def query_by_id(self, object_type, object_id):
        return None


class GraphHopperHandler(BaseHTTPRequestHandler):
    payload = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payload = json.loads(self.rfile.read(length).decode("utf-8"))
        start, destination = type(self).payload["points"]
        body = json.dumps({
            "paths": [{
                "distance": 1250.5,
                "time": 180000,
                "points": {
                    "type": "LineString",
                    "coordinates": [start, destination],
                },
                "instructions": [{"text": "向东行驶", "street_name": "测试道路"}],
                "snapped_waypoints": {
                    "type": "LineString",
                    "coordinates": [start, destination],
                },
            }],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class RoutePlanningTests(unittest.TestCase):
    def test_graphhopper_osm_export_uses_positive_shared_node_ids(self):
        source_data = {
            "elements": [
                {
                    "type": "way",
                    "id": 10,
                    "geometry": [
                        {"lon": 111.1, "lat": 24.1},
                        {"lon": 111.2, "lat": 24.2},
                    ],
                    "tags": {"highway": "residential", "name": "A&B"},
                },
                {
                    "type": "way",
                    "id": 11,
                    "geometry": [
                        {"lon": 111.2, "lat": 24.2},
                        {"lon": 111.3, "lat": 24.3},
                    ],
                    "tags": {"highway": "service"},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "roads.json"
            target = Path(directory) / "roads.osm.xml"
            source.write_text(json.dumps(source_data), encoding="utf-8")
            stats = build_osm_xml(source, target)
            root = ET.parse(target).getroot()

        node_ids = [int(node.attrib["id"]) for node in root.findall("node")]
        way_refs = [[nd.attrib["ref"] for nd in way.findall("nd")] for way in root.findall("way")]
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in root.findall("way")[0].findall("tag")}
        self.assertEqual({"ways": 2, "nodes": 3}, stats)
        self.assertTrue(all(node_id > 0 for node_id in node_ids))
        self.assertEqual(way_refs[0][-1], way_refs[1][0])
        self.assertEqual("A&B", tags["name"])

    def test_flood_cells_are_aggregated_to_bounded_polygon_areas(self):
        cells = [
            {
                "centroid_lon": 111.30 + index * 0.00005,
                "centroid_lat": 24.40 + row * 0.00005,
                "depth_m": 0.4,
            }
            for row in range(20)
            for index in range(20)
        ]
        result = route_planning.build_flood_avoidance_areas(cells, 0.3, max_areas=12)
        features = result["feature_collection"]["features"]

        self.assertTrue(features)
        self.assertLessEqual(len(features), 12)
        self.assertEqual("Polygon", features[0]["geometry"]["type"])
        self.assertEqual(400, result["summary"]["source_cell_count"])
        self.assertTrue(route_planning.point_in_areas(
            (111.3002, 24.4002), result["feature_collection"],
        ))

    def test_route_is_requested_and_saved_as_dynamic_route(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), GraphHopperHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                route_path = Path(directory) / "planned_routes.jsonl"
                with patch.object(route_planning, "PLANNED_ROUTES_PATH", route_path), patch.dict(
                    os.environ,
                    {"GRAPHHOPPER_URL": f"http://127.0.0.1:{server.server_port}"},
                ):
                    result = route_planning.plan_evacuation_route(
                        EmptyResolver(),
                        start_lon=111.30,
                        start_lat=24.40,
                        destination_lon=111.32,
                        destination_lat=24.41,
                        avoid_flood=False,
                    )

                self.assertEqual("completed", result["status"])
                self.assertEqual(1250.5, result["route"]["length_m"])
                self.assertEqual("LineString", json.loads(result["route"]["geometry"])["type"])
                self.assertEqual(result["route"]["route_id"], json.loads(route_path.read_text())["route_id"])
                self.assertNotIn("custom_model", GraphHopperHandler.payload)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
