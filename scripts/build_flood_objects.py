from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from domains.flood.build_objects import FloodObjectBuilder, write_object_library  # noqa: E402
from domains.flood.runtime.common import DATA_DIR, OBJECTS_DIR  # noqa: E402


OBJECT_TYPES = [
    "River",
    "Watershed",
    "Waterway",
    "HydrodynamicBoundary",
    "County",
    "Town",
    "Reservoir",
    "Sluice",
    "Bridge",
    "Facility",
    "HydraulicStructure",
    "Road",
    "Place",
    "Transfer",
    "Route",
    "Risk",
    "HydroStation",
    "Hydrology",
]


def build_objects(force: bool = False) -> dict:
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"source data directory not found: {DATA_DIR}")

    OBJECTS_DIR.mkdir(parents=True, exist_ok=True)
    builder = FloodObjectBuilder()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_data": str(DATA_DIR.relative_to(PROJECT_DIR)),
        "object_types": {},
        "notes": [],
    }

    for object_type in OBJECT_TYPES:
        target = OBJECTS_DIR / _filename_for(object_type)
        if target.exists() and not force:
            rows = _read_jsonl(target)
        else:
            rows = builder.build(object_type)
            target = write_object_library(object_type, rows)
        manifest["object_types"][object_type] = {
            "path": str(target.relative_to(PROJECT_DIR)),
            "count": len(rows),
        }

    manifest_path = OBJECTS_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def _filename_for(object_type: str) -> str:
    from domains.flood.runtime.common import OBJECT_LIBRARY_FILES

    return OBJECT_LIBRARY_FILES.get(object_type, f"{object_type.lower()}.jsonl")


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main():
    parser = argparse.ArgumentParser(description="Build flood domain object JSONL library.")
    parser.add_argument("--force", action="store_true", help="Rebuild even when target files already exist.")
    args = parser.parse_args()

    manifest = build_objects(force=args.force)
    print(json.dumps(manifest["object_types"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
