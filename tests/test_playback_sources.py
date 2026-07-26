from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from domains.flood.runtime.playback_sources import (
    BUILTIN_PLAYBACK_SOURCE_ID,
    PlaybackSourceRegistry,
    PlaybackSourceValidationError,
    REQUIRED_BOUNDARY_FLOW_COLUMNS,
    validate_playback_source,
)
from domains.flood.runtime.boundary_flow import load_boundary_flow_rows
from domains.flood.runtime.workspace import WorkspaceManager
from server.events import EventRuntime


BUILTIN_CSV = (
    PROJECT_DIR / "domains" / "flood" / "data" / "mock" / "boundary_flow.csv"
)


class PlaybackSourceValidationTest(unittest.TestCase):
    def test_accepts_utf8_bom_and_hourly_rows(self):
        content = b"\xef\xbb\xbf" + _csv_content(25)

        summary = validate_playback_source(content)

        self.assertEqual(summary["row_count"], 25)
        self.assertEqual(summary["start_time"], "2025-01-01 00:00")
        self.assertEqual(summary["end_time"], "2025-01-02 00:00")

    def test_accepts_excel_style_slash_dates_for_validation_and_playback(self):
        content = _csv_content(25, slash_dates=True)
        summary = validate_playback_source(content)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boundary_flow.csv"
            path.write_bytes(content)
            rows = load_boundary_flow_rows(path)

        self.assertEqual(summary["start_time"], "2025-01-01 00:00")
        self.assertEqual(summary["end_time"], "2025-01-02 00:00")
        self.assertEqual(rows[0]["observed_at"], "2025-01-01T00:00:00+08:00")
        self.assertEqual(rows[-1]["observed_at"], "2025-01-02T00:00:00+08:00")

    def test_requires_exact_columns(self):
        lines = _csv_content(25).decode("utf-8").splitlines()
        lines[0] = lines[0].replace(",end_level_m", "")

        with self.assertRaisesRegex(PlaybackSourceValidationError, "CSV 字段必须为"):
            validate_playback_source("\n".join(lines).encode("utf-8"))

    def test_rejects_non_hourly_or_short_data(self):
        content = _csv_content(25).replace(
            b"2025-01-01 12:00", b"2025-01-01 12:30"
        )
        with self.assertRaisesRegex(PlaybackSourceValidationError, "晚 1 小时"):
            validate_playback_source(content)

        with self.assertRaisesRegex(PlaybackSourceValidationError, "至少需要 25"):
            validate_playback_source(_csv_content(24))

    def test_rejects_invalid_numeric_value_and_extra_cell(self):
        content = _csv_content(25).replace(b",0.5,1.0,", b",not-a-number,1.0,", 1)
        with self.assertRaisesRegex(PlaybackSourceValidationError, "必须是数值"):
            validate_playback_source(content)

        lines = _csv_content(25).decode("utf-8").splitlines()
        lines[1] += ",extra"
        with self.assertRaisesRegex(PlaybackSourceValidationError, "字段之外"):
            validate_playback_source("\n".join(lines).encode("utf-8"))


class PlaybackSourceRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = PlaybackSourceRegistry(
            self.root / "sources",
            builtin_path=BUILTIN_CSV,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_lists_builtin_then_upload_and_persists_selection(self):
        initial = self.registry.list_sources()
        self.assertEqual(initial["selected_source_id"], BUILTIN_PLAYBACK_SOURCE_ID)
        self.assertEqual(len(initial["sources"]), 1)

        uploaded = self.registry.upload("example.csv", _csv_content(25))
        self.registry.select(uploaded.source_id)
        restored = PlaybackSourceRegistry(
            self.root / "sources",
            builtin_path=BUILTIN_CSV,
        )
        listed = restored.list_sources()

        self.assertEqual(listed["selected_source_id"], uploaded.source_id)
        self.assertEqual([item["kind"] for item in listed["sources"]], ["builtin", "uploaded"])
        self.assertTrue(listed["sources"][1]["selected"])

    def test_upload_rejects_non_csv_before_writing(self):
        with self.assertRaisesRegex(PlaybackSourceValidationError, "CSV 文件"):
            self.registry.upload("example.txt", _csv_content(25))
        self.assertFalse((self.root / "sources").exists())

    def test_snapshot_copies_source_and_metadata_into_workspace(self):
        uploaded = self.registry.upload("custom.csv", _csv_content(25))
        workspace = self.root / "workspace"

        snapshot, metadata = self.registry.snapshot(uploaded.source_id, workspace)

        self.assertEqual(snapshot.read_bytes(), _csv_content(25))
        self.assertEqual(metadata["source_id"], uploaded.source_id)
        stored = json.loads(
            (workspace / "inputs" / "playback_source.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["workspace_path"], "inputs/boundary_flow.csv")

    def test_runtime_switches_source_and_records_workspace_input(self):
        uploaded = self.registry.upload("custom.csv", _csv_content(25))
        manager = WorkspaceManager(self.root / "workspaces", retention_count=3)
        with patch("server.events.runtime.WORKSPACES", manager), patch(
            "domains.flood.runtime.workspace.WORKSPACES", manager
        ):
            runtime = EventRuntime(object(), playback_sources=self.registry)
            status = runtime.restart_playback(10, uploaded.source_id)

        workspace = manager.path(status["workspace_id"])
        manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(status["playback_source"]["source_id"], uploaded.source_id)
        self.assertEqual(status["total_rows"], 25)
        self.assertEqual(manifest["playback_source"]["source_id"], uploaded.source_id)
        self.assertTrue((workspace / "inputs" / "boundary_flow.csv").is_file())


def _csv_content(row_count: int, *, slash_dates: bool = False) -> bytes:
    lines = [",".join(REQUIRED_BOUNDARY_FLOW_COLUMNS)]
    start = datetime(2025, 1, 1)
    for index in range(row_count):
        observed_at = start + timedelta(hours=index)
        if slash_dates:
            timestamp = (
                f"{observed_at.year}/{observed_at.month}/{observed_at.day} "
                f"{observed_at.hour}:{observed_at.minute:02d}"
            )
        else:
            timestamp = f"{observed_at:%Y-%m-%d %H:%M}"
        lines.append(f"{timestamp},0.5,1.0,0.2,0.8,0.6,245.1")
    return ("\n".join(lines) + "\n").encode("utf-8")


if __name__ == "__main__":
    unittest.main()
