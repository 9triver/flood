"""Exercise both HTTP compositions in isolated processes and runtime directories."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SMOKE = r'''
import json
import os
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from server.app import create_server
from server.container import build_application
from server.flood_app import FloodApp
from server import dos_host
from domains.flood import dos_assets

root = Path(os.environ["FLOOD_RUNTIME_ROOT"])
mode = os.environ["RUNTIME_UNDER_TEST"]
dos_host.DEFAULT_JOURNAL = root / "dos" / "journal.jsonl"
dos_assets.DEFAULT_ARTIFACT_ROOT = root / "dos" / "assets"

with patch("server.container.FloodApp", side_effect=lambda: FloodApp(config={})):
    # Omitted runtime must keep the complete main business path as the default.
    context = build_application() if mode == "flood" else build_application(runtime=mode)
server = create_server("127.0.0.1", 0, context)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"

def get(path):
    try:
        with urlopen(base + path, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        error.add_note(error.read().decode())
        raise

try:
    bootstrap = get("/api/bootstrap")
    assert bootstrap["domain_os_query_enabled"] == (mode == "dos")
    assert "simulate_longtan_dispatch" in context.app.ontology.functions
    assert "get_longtan_dispatch_plan" in context.app.ontology.functions
    assert get("/api/autonomy/sources")
    status = get("/api/autonomy/status")
    assert status["playback_phase"] == "ready"
    mesh = get("/api/hydrodynamic-grid/meta?forecast_id=mesh")
    assert mesh["forecast"]["forecast_id"] == "mesh"
    assert mesh["feature_count"] > 0
    roads = get("/api/geojson?object_type=RoadRoute")
    assert len(roads["features"]) == 12
    if mode == "dos":
        assert context.dos_host is not None
        assert get("/api/domain/products")["items"] == []
        assert get("/api/domain/events")["head_cursor"] > 0
    else:
        assert context.dos_host is None
        try:
            get("/api/domain/products")
        except HTTPError as error:
            assert error.code == 503
        else:
            raise AssertionError("The default business server must not start an OS host")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    context.close()
'''


@pytest.mark.parametrize("runtime", ["flood", "dos"])
def test_server_runtime_http_surfaces(tmp_path, runtime):
    env = {
        **os.environ,
        "FLOOD_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "OAG_DATA_DIR": str(tmp_path / "agent"),
        "RUNTIME_UNDER_TEST": runtime,
        "DOS_FAKE_MODEL": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", SMOKE], cwd=PROJECT_DIR, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
