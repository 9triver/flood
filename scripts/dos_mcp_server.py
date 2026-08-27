"""dos MCP server hosting the flood demo instance (stdio transport).

Kernel runs in a background pump thread; a feeder thread plays the
station device so the world is alive for clients.  Use together with
scripts/check_dos_mcp.py, or any MCP client.

Env:
    DOS_JOURNAL   path for the durable journal (default: temp dir)
    DOS_ACT_GRANT 0 to refuse minting act capabilities at open_session
"""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, ".")

from dos import JsonlSink, Journal
from dos.gateway import DosGateway
from dos.mcp_server import build_mcp_server
from domains.flood.dos_instance import STATION, build_kernel

INTERVAL_METRIC = "sampling_interval_seconds"


def main() -> int:
    journal_path = os.environ.get("DOS_JOURNAL") or str(
        Path(tempfile.mkdtemp(prefix="dos-mcp-")) / "journal.jsonl"
    )
    kernel = build_kernel(journal=Journal(clock=time.time, sink=JsonlSink(journal_path)))
    driver = kernel.drivers[f"station-{STATION}"]

    stop = threading.Event()

    def feeder() -> None:
        """Play the station: level rises past the warning mark, then steady."""
        frames = [1.8, 2.6, 3.5, 3.6]
        index = 0
        while not stop.is_set():
            level = frames[min(index, len(frames) - 1)]
            index += 1
            kernel.interrupt(driver.device_id, {"level_m": level, "ts": time.time()})
            stop.wait(0.2)

    feeder_thread = threading.Thread(target=feeder, daemon=True, name="dos-feeder")
    feeder_thread.start()

    pump_thread = threading.Thread(target=kernel.run, kwargs={"idle_seconds": 0.05}, daemon=True, name="dos-pump")
    pump_thread.start()

    def shutdown() -> None:
        stop.set()
        kernel.stop()

    atexit.register(shutdown)

    gateway = DosGateway(kernel, allow_act_grant=os.environ.get("DOS_ACT_GRANT", "1") == "1")
    server = build_mcp_server(gateway, name="dos-flood")
    print(f"[dos-mcp-server] journal={journal_path} mount=/hydro/shanhu", file=sys.stderr)
    server.run("stdio")
    shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
