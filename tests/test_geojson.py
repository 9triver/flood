from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import geojson


class _Resolver:
    def __init__(self):
        self.name = "old"

    def query(self, object_type: str, filters: dict) -> list[dict]:
        return [{
            "reservoir_id": "longtan",
            "name": self.name,
            "geometry": json.dumps({
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
            }),
        }]


class GeojsonCacheTest(unittest.TestCase):
    def test_object_library_change_invalidates_cached_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "reservoir.jsonl"
            cache = root / "cache"
            source.write_text("old", encoding="utf-8")
            resolver = _Resolver()

            with (
                patch.object(geojson, "geojson_cache_dir", return_value=cache),
                patch.object(geojson, "object_library_path", return_value=source),
            ):
                first = geojson.export_objects_geojson(
                    resolver, "Reservoir", {"reservoir_id": "longtan"},
                )
                target = Path(first["absolute_path"])
                self.assertFalse(first["cached"])

                resolver.name = "new"
                newer = target.stat().st_mtime_ns + 1_000_000
                os.utime(source, ns=(newer, newer))
                second = geojson.export_objects_geojson(
                    resolver, "Reservoir", {"reservoir_id": "longtan"},
                )

            self.assertFalse(second["cached"])
            feature = json.loads(target.read_text(encoding="utf-8"))["features"][0]
            self.assertEqual("new", feature["properties"]["name"])


if __name__ == "__main__":
    unittest.main()
