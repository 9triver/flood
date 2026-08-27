"""dos server adapter: products/events mapping and playback over the CSV."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from server.dos_api import DosApi
from server.dos_host import DosFloodHost


def build_host() -> DosFloodHost:
    tmp = Path(tempfile.mkdtemp(prefix="dos-host-test-"))
    return DosFloodHost(journal_path=tmp / "journal.jsonl", fake_model=True)


def settle(host, predicate, timeout: float = 10.0):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() >= deadline:
            raise TimeoutError("condition not reached")
        time.sleep(0.05)


class TestDosApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = build_host()
        cls.host.start()
        # replay the whole scenario CSV through the feeder: mirror fills,
        # the trigger fires, forecast + impact + SSE events all appear
        while True:
            status = cls.host.step_playback()
            if status["sequence"] >= status["rows_total"]:
                break
        settle(cls.host, lambda: cls.host.kernel.namespace.try_read("/hydro/shanhu/forecasts/latest") is not None)
        cls.api = DosApi(cls.host)

    @classmethod
    def tearDownClass(cls):
        cls.host.stop()

    def test_products_shape(self):
        page = self.api.products(product_type="water.flood.forecast", limit=10)
        self.assertGreaterEqual(page["total"], 1)
        forecast = page["items"][0]
        self.assertTrue(forecast["product_id"].startswith("fcst_"))
        self.assertIsNone(forecast["data"]["parameters"]["time_h"])  # frontend predicate
        self.assertIn("artifacts", forecast["data"])
        impacts = self.api.products(product_type="water.flood.impact-assessment")
        self.assertGreaterEqual(impacts["total"], 1)
        impact = impacts["items"][-1]
        self.assertTrue(impact["input_refs"])  # links to forecast product id
        self.assertTrue(impact["input_refs"][0].startswith("fcst_"))
        single = self.api.product(forecast["product_id"])
        self.assertEqual(single["product_id"], forecast["product_id"])

    def test_events_and_cursor(self):
        page = self.api.events(after=0, limit=1000)
        generated = [item for item in page["items"] if item["event"]["event_type"] == "water.flood.forecast.generated"]
        self.assertGreaterEqual(len(generated), 1)
        self.assertTrue(generated[0]["event"]["data"]["product_id"].startswith("fcst_"))
        self.assertGreaterEqual(page["head_cursor"], generated[0]["cursor"])
        # filtering by type
        only_forecasts = self.api.events(after=0, event_type="water.flood.forecast.generated")
        self.assertGreaterEqual(only_forecasts["total"], 1)
        # cursor semantics: nothing new before the first event
        earlier = self.api.events(after=page["head_cursor"])
        self.assertEqual(earlier["items"], [])

    def test_playback_controls(self):
        status = self.host.start_playback()
        self.assertEqual(status["playback_phase"], "playing")
        status = self.host.pause_playback()
        self.assertEqual(status["playback_phase"], "paused")
        status = self.host.set_playback_speed(4)
        self.assertEqual(status["speed_multiplier"], 4)
        status = self.host.restart_playback()
        self.assertEqual(status["sequence"], 0)
        self.assertEqual(status["playback_phase"], "playing")
        status = self.host.stop_playback()
        self.assertEqual(status["playback_phase"], "stopped")
        self.host.pause_playback()  # leave parked for other tests


if __name__ == "__main__":
    unittest.main()
