from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from domains.flood.runtime.workspace import WorkspaceManager
from server.directives import DirectiveStore


class DirectiveStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspaces = WorkspaceManager(
            root=Path(self.temp_dir.name) / "workspaces",
            retention_count=3,
        )
        self.store = DirectiveStore(self.workspaces)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_issued_directive_is_appended_to_active_workspace(self):
        workspace_id = self.workspaces.create()["workspace_id"]
        record = self.store.issue({
            "workspace_id": workspace_id,
            "title": "组织新民村避洪转移",
            "content": "立即组织群众转移。",
            "recipients": "凤翔镇人民政府、新民村村委会",
            "priority": "urgent",
        }, {
            "observed_at": "2025-01-01T16:00:00+08:00",
            "forecast_version": 2,
        })

        path = (
            self.workspaces.path(workspace_id)
            / "directives"
            / "issued.jsonl"
        )
        stored = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(record, stored)
        self.assertEqual(workspace_id, record["workspace_id"])
        self.assertEqual("issued", record["status"])
        self.assertEqual(2, record["forecast_version"])
        self.assertRegex(record["directive_id"], r"^DIR-\d{8}-001$")

    def test_history_is_newest_first_and_ids_increment(self):
        workspace_id = self.workspaces.create()["workspace_id"]
        base = {
            "workspace_id": workspace_id,
            "content": "执行正文",
            "recipients": "凤翔镇人民政府",
            "priority": "normal",
        }
        first = self.store.issue({**base, "title": "第一份"}, {})
        second = self.store.issue({**base, "title": "第二份"}, {})

        history = self.store.list_issued()

        self.assertEqual([second, first], history["directives"])
        self.assertEqual("002", second["directive_id"].rsplit("-", 1)[-1])

    def test_rejects_missing_or_changed_workspace(self):
        with self.assertRaisesRegex(ValueError, "没有演进工作空间"):
            self.store.issue({"title": "测试"}, {})

        self.workspaces.create()
        with self.assertRaisesRegex(ValueError, "工作空间已切换"):
            self.store.issue({
                "workspace_id": "run_previous",
                "title": "测试",
                "content": "正文",
                "recipients": "接收对象",
            }, {})


if __name__ == "__main__":
    unittest.main()
