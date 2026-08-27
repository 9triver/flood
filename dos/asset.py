"""AssetDevice — reference-world assets in the namespace.

Assets (bridges, villages, rivers…) are not observations: they describe
what the world *contains*, not how it changes.  But agents must query
them, compute devices must resolve them, and updates must be audited —
so they live in the namespace behind a device, under

    {base}/manifest                 {version, counts, crs summary, ...}
    {base}/{Type}/{object_id}       attributes + geometry (inline or handle)

Disciplines:

- Geometry split: small geometries inline; anything over ``inline_max_chars``
  is written to disk as a GeoJSON artifact and the namespace keeps a handle
  (``geometry_available``) — the reader knows it exists and where to get it.
- CRS fidelity: each object carries its original ``geometry_crs`` string
  verbatim — including uncertain ones like "source_crs_unspecified…".
  The device never normalizes coordinates; transformation is a
  presentation concern.
- Bootstrap is idempotent: a boot check runs on the first pump and loads
  the library only when no manifest exists (journal recovery replays the
  assets instead of re-committing them).
- Updates are privileged transactions ("update_assets"): diff against the
  current library, upsert changed objects, tombstone removed ones, and
  re-commit the manifest; fsck confirms on fresh manifest evidence.

The device is domain-free; loaders (e.g. domains/flood/dos_assets.py)
map a domain's object library into the contract below.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from dos import Driver, Kernel
from dos.devices import PendingTxn

UPDATE_ASSETS = "update_assets"
UPDATE_DEADLINE_S = 120.0
DEFAULT_INLINE_MAX_CHARS = 1500

# loader() -> {"version": str, "objects": [{"type", "id", "attributes", "geometry", "geometry_crs"}]}
AssetLoader = Callable[[], dict]


def _vertex_count(geometry: Optional[dict]) -> int:
    if not isinstance(geometry, dict):
        return 0
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point":
        return 1
    if gtype in ("MultiPoint", "LineString") and isinstance(coords, list):
        return len(coords)
    if gtype in ("MultiLineString", "Polygon") and isinstance(coords, list):
        return sum(len(ring) for ring in coords)
    if gtype == "MultiPolygon" and isinstance(coords, list):
        return sum(len(ring) for poly in coords for ring in poly)
    return 0


class AssetDevice(Driver):
    device_id = "assets"
    privileged_actions = frozenset({UPDATE_ASSETS})
    default_txn_timeout = UPDATE_DEADLINE_S

    def __init__(
        self,
        base: str,
        loader: AssetLoader,
        *,
        artifact_root: Optional[Path] = None,
        inline_max_chars: int = DEFAULT_INLINE_MAX_CHARS,
    ):
        self.base = "/" + base.strip("/")
        self.loader = loader
        self.artifact_root = Path(artifact_root) if artifact_root else Path("/tmp/dos-assets")
        self.inline_max_chars = int(inline_max_chars)
        self.last_error: Optional[str] = None

    def attach(self, kernel) -> None:
        super().attach(kernel)
        # boot check is decided at the first pump (after journal recovery,
        # if any) so bootstrap never double-commits a recovered library
        kernel.interrupt(self.device_id, {"kind": "boot_check"})

    # ------------------------------------------------------------ normalize

    def normalize(self, raw: object) -> Iterable[tuple]:
        event = raw
        kind = event.get("kind")
        if kind == "boot_check":
            if self.kernel.namespace.try_read(self.path("manifest")) is not None:
                return  # library already present (journal recovery)
            library = self.loader()
            for record in self._materialize(library["objects"]):
                yield record
            yield self.path("manifest"), self._manifest(library, None), time.time()
        elif kind == "assets_updated":
            for record in self._materialize(event["upserts"]):
                yield record
            now = time.time()
            for object_path in event["removals"]:
                yield object_path, {"deleted": True, "version": event["version"]}, now
            yield self.path("manifest"), self._manifest(event["library"], event["txn_id"]), now
            yield self.path("last_update"), {"txn_id": event["txn_id"], "version": event["version"]}, now
        elif kind == "assets_error":
            yield self.path("last_update"), {"txn_id": event["txn_id"], "error": event["error"]}, time.time()
        else:
            self.last_error = f"unknown frame: {kind}"

    def _materialize(self, objects: Iterable[dict]):
        for obj in objects:
            value = dict(obj.get("attributes") or {})
            geometry = obj.get("geometry")
            crs = obj.get("geometry_crs")
            payload = json.dumps(geometry, ensure_ascii=False) if geometry is not None else None
            if payload is not None and len(payload) > self.inline_max_chars:
                artifact = self.artifact_root / obj["type"] / f"{obj['id']}.geojson"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text(payload, encoding="utf-8")
                value["geometry"] = {
                    "available": str(artifact),
                    "type": geometry.get("type"),
                    "vertices": _vertex_count(geometry),
                    "crs": crs,
                }
            else:
                value["geometry"] = geometry
                value["geometry_crs"] = crs
            yield self.path(f"{obj['type']}/{obj['id']}"), value, time.time()

    def _manifest(self, library: dict, txn_id: Optional[str]) -> dict:
        crs_summary: dict[str, int] = {}
        counts: dict[str, int] = {}
        for obj in library["objects"]:
            counts[obj["type"]] = counts.get(obj["type"], 0) + 1
            key = obj.get("geometry_crs") or "(none)"
            crs_summary[key] = crs_summary.get(key, 0) + 1
        return {
            "version": library["version"],
            "counts": counts,
            "crs_summary": crs_summary,
            "inline_max_chars": self.inline_max_chars,
            "last_update_txn": txn_id,
            "updated_at": time.time(),
        }

    # -------------------------------------------------------------- update

    def validate(self, path: str, action: str, args: dict) -> Optional[str]:
        if action != UPDATE_ASSETS:
            return f"unsupported action: {action}"
        return None

    def dispatch(self, txn: PendingTxn) -> None:
        threading.Thread(target=self._diff_and_update, args=(txn,), daemon=True, name=f"dos-{self.device_id}-update").start()

    def _diff_and_update(self, txn: PendingTxn) -> None:
        try:
            library = self.loader()
            current = self.kernel.namespace
            upserts, removals = [], []
            seen = set()
            for obj in library["objects"]:
                path = self.path(f"{obj['type']}/{obj['id']}")
                seen.add(path)
                existing = current.try_read(path)
                candidate = {"type": obj["type"], "id": obj["id"], "attributes": obj.get("attributes") or {}, "geometry": obj.get("geometry"), "geometry_crs": obj.get("geometry_crs")}
                if existing is None or _fingerprint(existing.value) != _fingerprint_obj(candidate, self.inline_max_chars):
                    upserts.append(candidate)
            if current.exists(self.path("manifest")):
                for object_path in current.paths(self.base):
                    if object_path.endswith("/manifest") or object_path.endswith("/last_update"):
                        continue
                    if object_path not in seen:
                        existing = current.try_read(object_path)
                        if existing is not None and not (isinstance(existing.value, dict) and existing.value.get("deleted")):
                            removals.append(object_path)
            self.kernel.interrupt(
                self.device_id,
                {"kind": "assets_updated", "txn_id": txn.txn_id, "version": library["version"],
                 "upserts": upserts, "removals": removals, "library": library},
            )
        except Exception as exc:  # noqa: BLE001 — update failure is evidence
            self.kernel.interrupt(self.device_id, {"kind": "assets_error", "txn_id": txn.txn_id, "error": f"{type(exc).__name__}: {exc}"})

    def verify(self, txn: PendingTxn, read) -> str:
        snap = read(self.path("last_update"))
        if snap is None:
            return "pending"
        evidence = snap.value
        if evidence.get("txn_id") != txn.txn_id:
            return "pending"
        if evidence.get("error"):
            return f"failed: {evidence['error']}"
        return "committed"

    def path(self, suffix: str) -> str:
        return f"{self.base}/{suffix.lstrip('/')}"


def _fingerprint_obj(candidate: dict, inline_max_chars: int) -> str:
    payload = dict(candidate.get("attributes") or {})
    geometry = candidate.get("geometry")
    if geometry is not None and len(json.dumps(geometry, ensure_ascii=False)) > inline_max_chars:
        payload["_geometry"] = {"type": geometry.get("type"), "vertices": _vertex_count(geometry), "crs": candidate.get("geometry_crs")}
    else:
        payload["_geometry"] = {"inline": geometry, "crs": candidate.get("geometry_crs")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _fingerprint(value: dict) -> str:
    payload = {k: v for k, v in (value or {}).items() if k not in ("geometry", "geometry_crs")}
    geometry = value.get("geometry")
    if isinstance(geometry, dict) and "available" in geometry:
        payload["_geometry"] = {"type": geometry.get("type"), "vertices": geometry.get("vertices"), "crs": geometry.get("crs")}
    else:
        payload["_geometry"] = {"inline": geometry, "crs": value.get("geometry_crs")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def mount_assets(kernel: Kernel, base: str, loader: AssetLoader, **kwargs) -> AssetDevice:
    device = AssetDevice(base, loader, **kwargs)
    kernel.mount(device.base, device)
    return device
