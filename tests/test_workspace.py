from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import route_planning
from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope


class WorkspaceTest(unittest.TestCase):
    def test_new_run_changes_workspace_but_scope_can_resume_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            first = manager.create()["workspace_id"]
            second = manager.create()["workspace_id"]

            self.assertNotEqual(first, second)
            self.assertEqual(manager.active_id, second)
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(first):
                    self.assertEqual(manager.active_id, first)
                self.assertEqual(manager.active_id, second)

    def test_dynamic_routes_are_isolated_by_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            first = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(first):
                    route_planning.save_planned_route({
                        "route_id": "route-first",
                        "start_object_type": "Transfer",
                        "start_object_id": "40",
                    })
                    self.assertEqual(
                        ["route-first"],
                        [row["route_id"] for row in route_planning.read_planned_routes()],
                    )

                second = manager.create()["workspace_id"]
                with workspace_scope(second):
                    self.assertEqual([], route_planning.read_planned_routes())

                with workspace_scope(first):
                    self.assertEqual(
                        ["route-first"],
                        [row["route_id"] for row in route_planning.read_planned_routes()],
                    )


if __name__ == "__main__":
    unittest.main()
