from __future__ import annotations

import unittest

from domains.flood.runtime.rainfall_runoff import (
    RainfallRunoffInputError,
    RainfallRunoffParameters,
    simulate_rainfall_runoff,
)


class RainfallRunoffTest(unittest.TestCase):
    def test_demo_defaults_match_configured_values(self):
        parameters = RainfallRunoffParameters()
        self.assertAlmostEqual(35.4326735909, parameters.area_km2)
        self.assertEqual(0.2, parameters.baseflow_m3s)
        self.assertEqual(0.8, parameters.runoff_coefficient)
        self.assertEqual(0.6, parameters.routing_alpha)
        self.assertEqual(1, parameters.lag_hours)
        self.assertEqual(0.5, parameters.dt_hours)
        result = simulate_rainfall_runoff([10])
        self.assertEqual(0.8, result["parameters"]["runoff_coefficient"])
        self.assertEqual(8.0, result["total_runoff_depth_mm"])

    def test_converts_rainfall_depth_to_water_balanced_inflow(self):
        result = simulate_rainfall_runoff(
            [{"valid_time": "2026-07-01 01:00", "rainfall_mm": 100}],
            area_km2=70,
            runoff_coefficient=0.3,
            routing_alpha=1.0,
            lag_hours=0,
            dt_hours=1.0,
        )

        point = result["series"][0]
        self.assertAlmostEqual(point["runoff_depth_mm"], 30.0)
        self.assertAlmostEqual(point["reservoir_inflow_m3s"], 583.533333, places=5)
        self.assertEqual("2026-07-01 01:00", point["valid_time"])

    def test_baseflow_lag_and_routing_smooth_the_peak(self):
        result = simulate_rainfall_runoff(
            [0, 100, 0],
            area_km2=70,
            runoff_coefficient=0.3,
            baseflow_m3s=2,
            routing_alpha=0.5,
            lag_hours=1,
            dt_hours=1.0,
        )

        flows = [item["reservoir_inflow_m3s"] for item in result["series"]]
        self.assertEqual([2.0, 2.0, 293.666667], flows)

    def test_rejects_invalid_parameters_and_rainfall(self):
        with self.assertRaises(RainfallRunoffInputError):
            simulate_rainfall_runoff([1], area_km2=0)
        with self.assertRaises(RainfallRunoffInputError):
            simulate_rainfall_runoff([-1], area_km2=70)


if __name__ == "__main__":
    unittest.main()
