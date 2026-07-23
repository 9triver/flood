from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime import hydrodynamic_grid
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

    def test_hydrodynamic_results_are_isolated_by_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            first = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with workspace_scope(first):
                    first_latest = hydrodynamic_grid.forecast_depth_path("latest")
                    first_named = hydrodynamic_grid.forecast_depth_path("scenario-a")

                second = manager.create()["workspace_id"]
                with workspace_scope(second):
                    second_latest = hydrodynamic_grid.forecast_depth_path("latest")
                    second_named = hydrodynamic_grid.forecast_depth_path("scenario-a")

            self.assertNotEqual(first_latest, second_latest)
            self.assertNotEqual(first_named, second_named)
            self.assertEqual(first_latest.name, "max_depth.csv")
            self.assertIn(first, str(first_latest))
            self.assertIn(second, str(second_named))

    def test_hydrodynamic_result_version_changes_with_forecast_output(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            workspace_id = manager.create()["workspace_id"]
            with patch("domains.flood.runtime.workspace.WORKSPACES", manager):
                with patch.object(hydrodynamic_grid, "PROJECT_DIR", Path(directory)):
                    with workspace_scope(workspace_id):
                        depth_path = hydrodynamic_grid.forecast_depth_path("latest")
                        depth_path.parent.mkdir(parents=True, exist_ok=True)
                        depth_path.write_text("cell_id,max_depth\n1,0.1\n", encoding="utf-8")
                        first = hydrodynamic_grid.forecast_stats("latest")["result_version"]
                        depth_path.write_text("cell_id,max_depth\n1,0.25\n", encoding="utf-8")
                        second = hydrodynamic_grid.forecast_stats("latest")["result_version"]

            self.assertNotEqual(first, second)

    def test_old_workspaces_are_pruned_to_retention_count(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory), retention_count=2)
            first = manager.create()["workspace_id"]
            second = manager.create()["workspace_id"]
            third = manager.create()["workspace_id"]

            self.assertFalse((Path(directory) / first).exists())
            self.assertTrue((Path(directory) / second).exists())
            self.assertTrue((Path(directory) / third).exists())


if __name__ == "__main__":
    unittest.main()
