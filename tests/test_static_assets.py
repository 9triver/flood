from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_DIR / "server" / "static"


class StaticAssetTest(unittest.TestCase):
    def test_direct_lucide_references_exist_in_local_bundle(self):
        bundle = (STATIC_DIR / "vendor" / "lucide.js").read_text(encoding="utf-8")
        available = set(re.findall(r"^\s*'([a-z0-9-]+)':", bundle, re.MULTILINE))
        referenced: set[str] = set()
        for filename in ("index.html", "app.js"):
            source = (STATIC_DIR / filename).read_text(encoding="utf-8")
            referenced.update(re.findall(r'data-lucide="([a-z0-9-]+)"', source))

        self.assertEqual(set(), referenced - available)

    def test_frontend_libraries_are_served_locally(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('/vendor/leaflet/leaflet.css?v=1.9.4', index)
        self.assertIn('/vendor/leaflet/leaflet.js?v=1.9.4', index)
        self.assertIn('/vendor/marked/marked.min.js?v=12.0.2', index)
        self.assertNotIn("unpkg.com", index)
        self.assertNotIn("cdn.jsdelivr.net", index)

        expected_files = (
            "vendor/leaflet/leaflet.css",
            "vendor/leaflet/leaflet.js",
            "vendor/leaflet/LICENSE",
            "vendor/leaflet/images/layers.png",
            "vendor/leaflet/images/layers-2x.png",
            "vendor/leaflet/images/marker-icon.png",
            "vendor/leaflet/images/marker-icon-2x.png",
            "vendor/leaflet/images/marker-shadow.png",
            "vendor/marked/marked.min.js",
            "vendor/marked/LICENSE.md",
        )
        for relative_path in expected_files:
            self.assertTrue((STATIC_DIR / relative_path).is_file(), relative_path)

    def test_impact_list_reuses_domain_map_symbols(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("objectIconInfo(impact.object_type, impact)", app)
        self.assertIn("FloodMapSymbols?.render(iconInfo.icon)", app)
        self.assertIn('class="impact-list-symbol object-symbol-', app)

    def test_evolution_controls_belong_to_situation_workbench(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        toolbar_start = index.index('<div class="map-toolbar">')
        workbench_start = index.index('id="situationWorkbench"')
        controls_start = index.index('class="situation-playback-controls"')
        toolbar_markup = index[toolbar_start:workbench_start]
        workbench_markup = index[workbench_start:]

        self.assertLess(toolbar_start, workbench_start)
        self.assertLess(workbench_start, controls_start)
        self.assertNotIn('id="playbackToggleBtn"', toolbar_markup)
        self.assertIn('id="playbackToggleBtn"', workbench_markup)
        self.assertNotIn('id="telemetryPanelBtn"', index)

    def test_evolution_controls_are_rendered_from_playback_phase(self):
        app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("function updatePlaybackControls(data = {})", app)
        self.assertIn('btn.hidden = !state.playbackPaused', app)
        self.assertIn('? "开始新演进"', app)


if __name__ == "__main__":
    unittest.main()
