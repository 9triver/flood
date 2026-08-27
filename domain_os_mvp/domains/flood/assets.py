from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from domain_os_mvp import (
    Driver,
    NormalizedObservation,
    Observation,
    Operation,
    Verification,
)

from .paths import ASSETS_BASE, asset_index_path, asset_path


PROJECT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_OBJECTS_DIR = PROJECT_DIR / "domains" / "flood" / "data" / "objects"


class FloodAssetDriver(Driver):
    """Mount the versioned flood GIS object library as read-only resources."""

    device_id = "flood:assets"
    operation_timeout_seconds = None

    def __init__(self, objects_dir: Path = DEFAULT_OBJECTS_DIR):
        self.objects_dir = Path(objects_dir)

    def bootstrap(self) -> None:
        self.kernel.interrupt(
            self.device_id,
            {"kind": "load_library", "observed_at": self.kernel.clock()},
        )

    def normalize(self, raw: object) -> Iterable[NormalizedObservation]:
        if not isinstance(raw, dict) or raw.get("kind") != "load_library":
            raise ValueError("asset driver accepts only load_library frames")
        observed_at = float(raw["observed_at"])
        manifest_path = self.objects_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        total = 0
        counts = {}
        for object_type, specification in sorted(manifest["object_types"].items()):
            source_path = PROJECT_DIR / specification["path"]
            if not source_path.exists():
                source_path = self.objects_dir / Path(specification["path"]).name
            references = []
            count = 0
            for line in source_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                object_id = _object_id(object_type, row)
                reference = asset_path(object_type, object_id)
                references.append(reference)
                count += 1
                geometry = row.get("geometry")
                if isinstance(geometry, str) and geometry.strip():
                    geometry = json.loads(geometry)
                attributes = {
                    key: value
                    for key, value in row.items()
                    if key != "geometry"
                }
                yield NormalizedObservation(
                    reference,
                    {
                        "kind": "asset",
                        "object_type": object_type,
                        "object_id": object_id,
                        "attributes": attributes,
                        "geometry": geometry,
                        "geometry_crs": row.get("geometry_crs"),
                        "source_ref": specification["path"],
                    },
                    observed_at,
                    self.device_id,
                )
            counts[object_type] = count
            total += count
            yield NormalizedObservation(
                asset_index_path(object_type),
                {
                    "object_type": object_type,
                    "count": count,
                    "refs": references,
                },
                observed_at,
                self.device_id,
            )
        yield NormalizedObservation(
            ASSETS_BASE,
            {
                "kind": "asset_catalog",
                "domain": "flood",
                "watershed": "shanhu",
                "version": manifest.get("generated_at"),
                "object_count": total,
                "counts": counts,
                "source_data": manifest.get("source_data"),
            },
            observed_at,
            self.device_id,
        )

    def validate(self, path: str, action: str, arguments: dict) -> str | None:
        return "asset catalog is read-only"

    def dispatch(self, operation: Operation) -> None:
        raise RuntimeError("asset catalog is read-only")

    def verify(
        self,
        operation: Operation,
        evidence: Sequence[Observation],
    ) -> Verification:
        return Verification.pending()


def _object_id(object_type: str, row: dict) -> str:
    preferred = {
        "HydrodynamicBoundary": "boundary_id",
        "HydraulicStructure": "structure_id",
    }.get(object_type, f"{_snake_case(object_type)}_id")
    if row.get(preferred) not in (None, ""):
        return str(row[preferred])
    for key, value in row.items():
        if key.endswith("_id") and value not in (None, ""):
            return str(value)
    raise ValueError(f"{object_type} object has no stable id")


def _snake_case(value: str) -> str:
    result = []
    for index, character in enumerate(value):
        if index and character.isupper():
            result.append("_")
        result.append(character.lower())
    return "".join(result)
