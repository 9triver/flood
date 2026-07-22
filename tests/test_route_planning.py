from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from domains.flood.runtime import route_planning


class EmptyResolver:
    def query_by_id(self, object_type, object_id):
        return None


class AMapHandler(BaseHTTPRequestHandler):
    path_requested = ""

    @classmethod
    def route_paths(cls, origin, destination):
        return [amap_v5_path(
            f"{origin};{destination}",
            distance=976,
            duration=781,
            road_name="无名路",
        )]

    def do_GET(self):
        type(self).path_requested = self.path
        params = parse_qs(urlparse(self.path).query)
        origin = (params.get("origin") or [""])[0]
        destination = (params.get("destination") or [""])[0]
        body = json.dumps({
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "count": "1",
            "route": {
                "paths": type(self).route_paths(origin, destination),
            },
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class AlternativeAMapHandler(AMapHandler):
    @classmethod
    def route_paths(cls, origin, destination):
        origin_lon, origin_lat = (float(value) for value in origin.split(","))
        destination_lon, destination_lat = (
            float(value) for value in destination.split(",")
        )
        midpoint = (
            f"{(origin_lon + destination_lon) / 2:.6f},"
            f"{max(origin_lat, destination_lat) + 0.020000:.6f}"
        )
        return [
            amap_v5_path(
                f"{origin};{destination}",
                distance=976,
                duration=781,
                road_name="直行道路",
            ),
            amap_v5_path(
                f"{origin};{midpoint};{destination}",
                distance=1300,
                duration=1040,
                road_name="北侧绕行道路",
            ),
        ]


def amap_v5_path(polyline, *, distance, duration, road_name):
    return {
        "distance": str(distance),
        "cost": {"duration": str(duration)},
        "steps": [{
            "instruction": "沿道路步行",
            "road_name": road_name,
            "step_distance": str(distance),
            "cost": {"duration": str(duration)},
            "polyline": polyline,
        }],
    }


class RoutePlanningTests(unittest.TestCase):
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

    def test_amap_route_is_converted_to_wgs84_and_saved(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AMapHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                route_path = Path(directory) / "planned_routes.jsonl"
                cached_geojson = Path(directory) / "route.geojson"
                cached_geojson.write_text("{}", encoding="utf-8")
                with patch.object(
                    route_planning, "planned_routes_path", return_value=route_path,
                ), patch.object(
                    route_planning, "clear_route_geojson_cache",
                    side_effect=lambda: cached_geojson.unlink(missing_ok=True),
                ), patch.dict(
                    os.environ,
                    {
                        "AMAP_WEB_SERVICE_KEY": "test-web-service-key",
                        "AMAP_WEB_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
                    },
                ):
                    result = route_planning.plan_evacuation_route(
                        EmptyResolver(),
                        start_lon=111.30,
                        start_lat=24.40,
                        destination_lon=111.32,
                        destination_lat=24.41,
                        profile="foot",
                        avoid_flood=False,
                    )

                self.assertEqual("completed", result["status"])
                self.assertEqual("AMap", result["route"]["routing_engine"])
                self.assertEqual(976.0, result["route"]["length_m"])
                geometry = json.loads(result["route"]["geometry"])
                self.assertAlmostEqual(111.30, geometry["coordinates"][0][0], places=5)
                self.assertAlmostEqual(24.40, geometry["coordinates"][0][1], places=5)
                request = json.loads(result["route"]["routing_request"])
                self.assertNotIn("key", json.dumps(request))
                self.assertIn("/v5/direction/walking?", AMapHandler.path_requested)
                query = parse_qs(urlparse(AMapHandler.path_requested).query)
                self.assertEqual(["3"], query["alternative_route"])
                self.assertEqual(["cost,polyline"], query["show_fields"])
                self.assertEqual(1, result["routing_diagnostics"]["candidate_count"])
                self.assertEqual(1, result["routing_diagnostics"]["selected_candidate_index"])
                self.assertFalse(cached_geojson.exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_amap_route_crossing_flood_area_is_rejected(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AMapHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                route_path = Path(directory) / "planned_routes.jsonl"
                with patch.object(
                    route_planning, "planned_routes_path", return_value=route_path,
                ), patch.object(
                    route_planning, "clear_route_geojson_cache",
                ), patch.object(
                    route_planning,
                    "query_forecast_cells",
                    return_value=[{
                        "centroid_lon": 111.31,
                        "centroid_lat": 24.405,
                        "depth_m": 0.4,
                    }],
                ), patch.dict(os.environ, {
                    "AMAP_WEB_SERVICE_KEY": "test-web-service-key",
                    "AMAP_WEB_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
                }):
                    result = route_planning.plan_evacuation_route(
                        EmptyResolver(),
                        start_lon=111.30,
                        start_lat=24.40,
                        destination_lon=111.32,
                        destination_lat=24.41,
                        profile="foot",
                        avoid_flood=True,
                        blocked_depth_m=0.3,
                    )

                self.assertEqual("no_safe_route", result["status"])
                self.assertEqual("AMap", result["routing_engine"])
                self.assertIn("预测淹没约束", result["error"])
                diagnostics = result["routing_diagnostics"]
                self.assertEqual(1, diagnostics["candidate_count"])
                self.assertEqual(0, diagnostics["safe_candidate_count"])
                self.assertEqual(
                    "intersects_flood",
                    diagnostics["rejected_candidates"][0]["reason"],
                )
                self.assertFalse(route_path.exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_second_amap_candidate_is_selected_when_first_crosses_flood(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), AlternativeAMapHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                route_path = Path(directory) / "planned_routes.jsonl"
                with patch.object(
                    route_planning, "planned_routes_path", return_value=route_path,
                ), patch.object(
                    route_planning, "clear_route_geojson_cache",
                ), patch.object(
                    route_planning,
                    "query_forecast_cells",
                    return_value=[{
                        "centroid_lon": 111.31,
                        "centroid_lat": 24.405,
                        "depth_m": 0.4,
                    }],
                ), patch.dict(os.environ, {
                    "AMAP_WEB_SERVICE_KEY": "test-web-service-key",
                    "AMAP_WEB_SERVICE_URL": f"http://127.0.0.1:{server.server_port}",
                }):
                    result = route_planning.plan_evacuation_route(
                        EmptyResolver(),
                        start_lon=111.30,
                        start_lat=24.40,
                        destination_lon=111.32,
                        destination_lat=24.41,
                        profile="foot",
                        avoid_flood=True,
                        blocked_depth_m=0.3,
                    )

                self.assertEqual("completed", result["status"])
                diagnostics = result["routing_diagnostics"]
                self.assertEqual(2, diagnostics["candidate_count"])
                self.assertEqual(1, diagnostics["safe_candidate_count"])
                self.assertEqual(2, diagnostics["selected_candidate_index"])
                self.assertEqual(
                    "intersects_flood",
                    diagnostics["rejected_candidates"][0]["reason"],
                )
                self.assertEqual(2, result["route"]["selected_candidate_index"])
                self.assertEqual(1300.0, result["route"]["length_m"])
                self.assertTrue(route_path.exists())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
