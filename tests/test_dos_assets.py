"""Asset device: bootstrap, geometry split, CRS fidelity, update transactions."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from dos import Journal, Kernel, mount_assets
from dos.persistence import JsonlSink, load_journal, recover

BASE = "/test/assets"
MANIFEST = f"{BASE}/manifest"

SMALL = {"type": "Point", "coordinates": [111.0, 24.0]}
BIG = {"type": "LineString", "coordinates": [[float(i), 24.0] for i in range(200)]}  # > 1500 chars


def library(version="v1", include_big=True, rename=None):
    objects = [
        {"type": "Bridge", "id": "bridge_1", "attributes": {"name": "老桥", "deck_elevation_m": 210.0}, "geometry": SMALL, "geometry_crs": "EPSG:4326"},
        {"type": "Station", "id": "st_1", "attributes": {"name": "站"}, "geometry": None, "geometry_crs": "source_crs_unspecified_assumed_wgs84"},
    ]
    if include_big:
        objects.append({"type": "River", "id": "river_1", "attributes": {"name": "珊瑚河"}, "geometry": BIG, "geometry_crs": "EPSG:4326"})
    if rename:
        objects[0]["attributes"]["name"] = rename
    return {"version": version, "objects": objects}


def make_kernel(loader_state, artifact_root: Path) -> Kernel:
    kernel = Kernel(clock=lambda: time.time())
    mount_assets(kernel, BASE, lambda: library(**loader_state["params"]), artifact_root=artifact_root)
    kernel.grant(BASE, {"update_assets"}, "test")
    return kernel


class TestBootstrap(unittest.TestCase):
    def test_load_inline_and_artifact_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"params": {}}
            kernel = make_kernel(state, root)
            kernel.pump()
            bridge = kernel.read(f"{BASE}/Bridge/bridge_1").value
            self.assertEqual(bridge["geometry"], SMALL)  # inline
            self.assertEqual(bridge["geometry_crs"], "EPSG:4326")
            river = kernel.read(f"{BASE}/River/river_1").value
            self.assertIn("available", river["geometry"])  # artifact handle
            self.assertEqual(river["geometry"]["vertices"], 200)
            artifact = Path(river["geometry"]["available"])
            self.assertTrue(artifact.exists())
            self.assertEqual(json.loads(artifact.read_text())["type"], "LineString")
            manifest = kernel.read(MANIFEST).value
            self.assertEqual(manifest["counts"], {"Bridge": 1, "Station": 1, "River": 1})
            self.assertEqual(manifest["crs_summary"], {"EPSG:4326": 2, "source_crs_unspecified_assumed_wgs84": 1})

    def test_bootstrap_idempotent_and_recovery_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_path = str(Path(tmp) / "journal.jsonl")
            k1 = Kernel(journal=Journal(clock=time.time, sink=JsonlSink(journal_path)), clock=time.time)
            mount_assets(k1, BASE, lambda: library(), artifact_root=root)
            k1.pump()
            after_boot = len([r for r in k1.journal.replay() if r.kind == "observation"])

            k1.pump()  # second pump must not re-commit the library
            self.assertEqual(len([r for r in k1.journal.replay() if r.kind == "observation"]), after_boot)

            # reboot from journal: recovery replays assets, boot check stays quiet
            k2 = Kernel(journal=load_journal(journal_path), clock=time.time)
            mount_assets(k2, BASE, lambda: library(), artifact_root=root)
            recover(k2)
            before = len([r for r in k2.journal.replay() if r.kind == "observation"])
            k2.pump()
            after = len([r for r in k2.journal.replay() if r.kind == "observation"])
            self.assertEqual(before, after)  # no double-commit after recovery
            self.assertEqual(k2.read(MANIFEST).value["counts"]["Bridge"], 1)


class TestUpdateTransaction(unittest.TestCase):
    def test_diff_update_tombstone_and_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"params": {}}
            kernel = make_kernel(state, root)
            cap = kernel.grant(BASE, {"update_assets"}, "test")
            kernel.pump()
            old_version = kernel.read(MANIFEST).value["version"]

            # library changes: bridge renamed, river removed, version bumped
            state["params"] = {"version": "v2", "include_big": False, "rename": "新桥"}
            result = kernel.act(cap.token, f"{BASE}/manifest", "update_assets")
            self.assertEqual(result.state, "awaiting_approval")  # privileged
            result = kernel.approve(result.txn_id, approved_by="管理员", decision=True, reason="对象库更新")
            deadline = time.time() + 5
            while kernel.txn(result.txn_id).state == "dispatched" and time.time() < deadline:
                kernel.pump()
                time.sleep(0.02)
            self.assertEqual(kernel.txn(result.txn_id).state, "committed")

            manifest = kernel.read(MANIFEST).value
            self.assertEqual(manifest["version"], "v2")
            self.assertNotEqual(manifest["version"], old_version)
            self.assertEqual(kernel.read(f"{BASE}/Bridge/bridge_1").value["name"], "新桥")
            self.assertEqual(kernel.read(f"{BASE}/River/river_1").value.get("deleted"), True)  # tombstone

    def test_noop_update_still_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"params": {}}
            kernel = make_kernel(state, root)
            cap = kernel.grant(BASE, {"update_assets"}, "test")
            kernel.pump()
            result = kernel.act(cap.token, f"{BASE}/manifest", "update_assets")
            kernel.approve(result.txn_id, approved_by="管理员", decision=True)
            deadline = time.time() + 5
            while kernel.txn(result.txn_id).state == "dispatched" and time.time() < deadline:
                kernel.pump()
                time.sleep(0.02)
            self.assertEqual(kernel.txn(result.txn_id).state, "committed")

    def test_flood_library_loads(self):
        from domains.flood.dos_assets import load_flood_library

        lib = load_flood_library()
        self.assertGreater(len(lib["objects"]), 1600)
        self.assertEqual(len(lib["version"]), 16)
        types = {obj["type"] for obj in lib["objects"]}
        self.assertIn("Bridge", types)
        self.assertIn("EvacuationSite", types)


if __name__ == "__main__":
    unittest.main()
