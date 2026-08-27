"""Flood object library as dos assets.

Maps domains/flood/data/objects (16 types, ~1666 objects, GeoJSON geometry
strings with per-object CRS annotations) into the AssetDevice contract.
Geometry parsing and CRS strings are carried verbatim — including
uncertain annotations like "source_crs_unspecified_assumed_wgs84".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from dos import Kernel
from dos.asset import AssetDevice, mount_assets

OBJECTS_DIR = Path(__file__).resolve().parent / "data" / "objects"
ASSETS_BASE = "/hydro/shanhu/assets"
PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = PROJECT_DIR / "local" / "runtime" / "dos" / "assets"


def load_flood_library(objects_dir: Path = OBJECTS_DIR) -> dict:
    manifest = json.loads((objects_dir / "manifest.json").read_text(encoding="utf-8"))
    objects = []
    digest = hashlib.sha256()
    for type_name, spec in sorted(manifest["object_types"].items()):
        path = PROJECT_DIR / spec["path"]
        rows = path.read_text(encoding="utf-8").splitlines()
        digest.update(path.read_bytes())
        id_field_candidates = [f"{path.stem}_id", f"{type_name.lower()}_id"]
        for line in rows:
            if not line.strip():
                continue
            row = json.loads(line)
            id_field = next((f for f in id_field_candidates if f in row), next((k for k in row if k.endswith("_id")), None))
            if id_field is None:
                raise ValueError(f"{type_name} row has no id field: {sorted(row)[:5]}")
            attributes = {k: v for k, v in row.items() if k not in ("geometry",)}
            geometry = None
            if isinstance(row.get("geometry"), str) and row["geometry"].strip():
                geometry = json.loads(row["geometry"])
            objects.append(
                {
                    "type": type_name,
                    "id": str(row[id_field]),
                    "attributes": attributes,
                    "geometry": geometry,
                    "geometry_crs": row.get("geometry_crs"),
                }
            )
    return {"version": digest.hexdigest()[:16], "objects": objects}


def mount_flood_assets(kernel: Kernel, *, artifact_root: Optional[Path] = None) -> AssetDevice:
    return mount_assets(
        kernel,
        ASSETS_BASE,
        load_flood_library,
        artifact_root=artifact_root or DEFAULT_ARTIFACT_ROOT,
    )
