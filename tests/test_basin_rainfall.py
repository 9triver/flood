from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from domains.flood.runtime.boundary_flow import load_boundary_flow_rows, BoundaryFlowPlaybackSource
from domains.flood.runtime.playback_sources import validate_playback_source, PlaybackSourceValidationError
from domains.flood.runtime.rainfall_input import REQUIRED_RAINFALL_COLUMNS


def rainfall_csv(pulse):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(REQUIRED_RAINFALL_COLUMNS)
    for i in range(25):
        writer.writerow([(datetime(2026, 7, 1) + timedelta(hours=i)).strftime('%Y-%m-%d %H:%M'),
                         *(pulse if i == 0 else [0, 0, 0])])
    return output.getvalue().encode()


class BasinRainfallTest(unittest.TestCase):
    def test_forecast_exposes_separate_future_basin_rainfall(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rainfall.csv'
            path.write_bytes(rainfall_csv([10, 20, 30]))
            source = BoundaryFlowPlaybackSource(path, Path(directory) / 'observations.jsonl')
            observation = source.next_observation()
            future = observation['rainfall_forecast']
            self.assertEqual(len(future), 24)
            for field in REQUIRED_RAINFALL_COLUMNS[1:]:
                self.assertGreater(observation[field], 0)
                self.assertEqual(sum(point[field] for point in future), 0)
            source.index = 24
            self.assertEqual(source.next_observation()['rainfall_forecast'], [])

    def load(self, pulse):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rainfall.csv'
            path.write_bytes(rainfall_csv(pulse))
            return load_boundary_flow_rows(path)

    def test_basin_forcing_is_independent_and_tonggu_follows_interval2(self):
        dry = self.load([0, 0, 0])
        for column, boundary, area in [(0, 'interval1', 381), (1, 'interval2', 85)]:
            pulse = [0, 0, 0]
            pulse[column] = 10
            rows = self.load(pulse)
            self.assertAlmostEqual(rows[1]['boundaries'][boundary]['flow_m3s'],
                                   0.2 + 0.6 * 10 * 0.5 * area / 3.6, places=5)
            other = 'interval2' if boundary == 'interval1' else 'interval1'
            for row, baseline in zip(rows, dry):
                self.assertEqual(row['boundaries'][other], baseline['boundaries'][other])
                self.assertEqual(row['reservoir_inflow_m3s'], baseline['reservoir_inflow_m3s'])
                self.assertEqual(row['reservoir_release_m3s'], baseline['reservoir_release_m3s'])
                self.assertAlmostEqual(row['boundaries']['tonggu']['flow_m3s'],
                                       row['boundaries']['interval2']['flow_m3s'] * 0.946, places=5)
        wet = self.load([0, 0, 100])
        self.assertAlmostEqual(wet[1]['reservoir_inflow_m3s'], 300.2)
        self.assertGreater(max(r['reservoir_release_m3s'] for r in wet), 0)
        for row, baseline in zip(wet, dry):
            self.assertEqual(row['boundaries']['interval1'], baseline['boundaries']['interval1'])
            self.assertEqual(row['boundaries']['interval2'], baseline['boundaries']['interval2'])
            self.assertEqual(row['boundaries']['upstream']['flow_m3s'], row['reservoir_release_m3s'])

    def test_display_mean_and_station_values_do_not_replace_basin_rainfall(self):
        first = self.load([10, 20, 30])[0]
        mean = (10 * 381 + 20 * 85 + 30 * 36) / 502
        self.assertAlmostEqual(first['rainfall_mm'], mean, places=3)
        self.assertAlmostEqual(sum(x['rainfall_mm'] for x in first['station_rainfall']) / 12, mean, places=3)
        self.assertEqual(first['reservoir_rainfall_mm'], 30)

    def test_invalid_basin_rainfall_rejected(self):
        for value in [-1, float('nan'), float('inf')]:
            with self.subTest(value=value), self.assertRaises(PlaybackSourceValidationError):
                validate_playback_source(rainfall_csv([0, value, 0]))
        old = rainfall_csv([0, 0, 0]).replace(b'interval1_rainfall_mm', b'rainfall_mm')
        with self.assertRaises(PlaybackSourceValidationError):
            validate_playback_source(old)
