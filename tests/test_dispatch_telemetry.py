import tempfile
import unittest
from pathlib import Path
from domains.flood.runtime.boundary_flow import BoundaryFlowPlaybackSource
from domains.flood.runtime.reservoir_dispatch import simulate_reservoir_dispatch


class DispatchTelemetryTest(unittest.TestCase):
    def test_prerelease_explains_zero_capacity(self):
        result = simulate_reservoir_dispatch([0.2] + [100.0] * 24)
        row = result['series'][0]
        self.assertEqual(row['state'], 'PRERELEASE')
        self.assertEqual(row['reason_code'], 'forecast_above_flood_limit')
        self.assertEqual(row['available_release_m3s'], 0)
        self.assertEqual(row['release_m3s'], 0)
        self.assertIn('能力为零', row['constraint'])
        self.assertEqual(row['thresholds']['flood_limit_level_m'], 245.3)

    def test_actual_and_forecast_dispatch_use_same_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = BoundaryFlowPlaybackSource(observation_path=Path(directory) / 'observations.jsonl')
            source.index = 54
            observation = source.next_observation()
            self.assertEqual(observation['reservoir_dispatch'], source.rows[54]['reservoir_dispatch'])
            future = observation['reservoir_forecast']['series']
            self.assertEqual(len(future), 24)
            for point, row in zip(future, source.rows[55:79]):
                decision = point['reservoir_dispatch']
                self.assertEqual(decision, row['reservoir_dispatch'])
                self.assertEqual(decision['valid_time'], point['valid_time'])
                self.assertEqual(decision['release_m3s'], point['reservoir_release_m3s'])
