"""Exercise query consumers without mocking the query function signatures."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domains.flood.runtime.workspace import WorkspaceManager, workspace_scope
from domains.flood.runtime.repository import FloodRepository
from domains.flood.runtime.impact_analysis import analyze_inundation_impacts
from domains.flood.runtime.route_planning import plan_route
from domains.flood.runtime.forecast_query import clear_forecast_cell_cache


class ForecastConsumersTest(unittest.TestCase):
    def test_empty_workspace_queries_counts_impacts_and_route_input(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WorkspaceManager(Path(directory))
            wid = manager.create()['workspace_id']
            with patch('domains.flood.runtime.workspace.WORKSPACES', manager), workspace_scope(wid):
                repo = FloodRepository()
                for kind in ('FloodForecast', 'InundationForecastCell'):
                    self.assertEqual(repo.query(kind), [])
                    self.assertEqual(repo.count(kind), 0)
                result = analyze_inundation_impacts(repo, target_type='Road')
                self.assertEqual(result['status'], 'no_forecast_cells')
                with patch('domains.flood.runtime.route_planning.routing_setting', return_value=''):
                    result = plan_route(repo, start_lon=111.3, start_lat=24.4,
                                                   destination_lon=111.31, destination_lat=24.41)
                self.assertEqual(result['status'], 'forecast_unavailable')
        clear_forecast_cell_cache()
