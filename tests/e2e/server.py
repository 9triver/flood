"""Isolated E2E server: actual app/tools, small model output fixture, fake AMap transport.

Replay scripts tool calls to verify integration; --live uses the configured LLM.
No test routes or fixtures are installed in the production HTTP handler.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(PROJECT), str(PROJECT / "agent")]
parser = argparse.ArgumentParser()
parser.add_argument("--runtime-root", type=Path, required=True)
parser.add_argument("--ready-file", type=Path, required=True)
parser.add_argument("--live", action="store_true")
parser.add_argument("--parent-pid", type=int, required=True)
args = parser.parse_args()
os.environ["FLOOD_RUNTIME_ROOT"] = str(args.runtime_root.resolve())


def exit_if_runner_died():
    while True:
        try:
            os.kill(args.parent_pid, 0)
        except ProcessLookupError:
            os._exit(0)
        time.sleep(1)


threading.Thread(target=exit_if_runner_died, daemon=True).start()

import numpy as np
from http.server import ThreadingHTTPServer
from oag.harness import Harness
from oag.runtime import HarnessConfig, ToolUseContext
from oag.runtime.events import TextEvent, ToolCallEvent, ToolResultEvent
from server.app import Handler
from server.container import ApplicationContext
from server.flood_app import FloodApp
from server.chat.service import FloodChatService
from server.chat.agent_factory import load_env, configure_agent_query_tools, configure_domain_tool_schemas
from server.presentation.map_tools import register_map_tools
from server.presentation.directive_tools import register_directive_tools
from server.agent_runs import AgentRunManager
from server.events import EventRuntime
from server.directives import DirectiveStore
from domains.flood.runtime.workspace import WORKSPACES, workspace_dir
from domains.flood.runtime import route_planning, hydrodynamic_grid as grid

WORKSPACES.begin_session()
config = load_env(PROJECT / ".env") if args.live else {}
config.update({"OAG_DATA_DIR": str(args.runtime_root / "agent"),
               "GENAI_TRACE_JSON_PATH": str(args.runtime_root / "agent/genai.json"),
               "AMAP_WEB_SERVICE_KEY": "e2e-transport"})
app = FloodApp(config=config)


def fake_amap(key, payload, timeout):
    origin, destination = payload["params"]["origin"], payload["params"]["destination"]
    return {"status": "1", "route": {"paths": [{"distance": "850", "cost": {"duration": "900"},
        "steps": [{"instruction": "沿测试路线到达安置点", "step_distance": "850", "cost": {"duration": "900"},
                   "polyline": f"{origin};{destination}"}]}]}}


route_planning.call_amap = fake_amap
route_planning.routing_setting = lambda name, default: "e2e-transport" if name == "AMAP_WEB_SERVICE_KEY" else default

# A small real SQLite mesh and NumPy output, consumed by the production calculations.
centers = [(app.resolver.query_by_id(kind, ident)["longitude"], app.resolver.query_by_id(kind, ident)["latitude"])
           for kind, ident in [("EvacuationUnit", "43"), ("EvacuationSite", "shelter_235"), ("EvacuationSite", "in_place_522")]]
mesh_rows = []
for index, (lon, lat) in enumerate(centers, 1):
    r = 0.00012
    mesh_rows.append((index, lon-r, lat-r, lon+r, lat+2*r, lon-r, lat-r, lon+r, lat-r, lon, lat+2*r))
meta = {"feature_count": len(mesh_rows), "min_lon": min(p[0] for p in centers)-0.001,
        "max_lon": max(p[0] for p in centers)+0.001, "min_lat": min(p[1] for p in centers)-0.001,
        "max_lat": max(p[1] for p in centers)+0.001}
grid.STORE._database.parse_cells = lambda: (mesh_rows, dict(meta))
grid.STORE.ensure_ready()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def seed_forecast(wet_now=False):
    version = "v002" if wet_now else "v001"
    clock = "2026-07-03T09:00:00+08:00" if wet_now else "2026-07-03T08:00:00+08:00"
    root = workspace_dir()
    summary = {"boundary_flow_id": version, "window_start": "2026-07-03T08:00:00+08:00", "window_end": "2026-07-04T08:00:00+08:00"}
    metadata = {"workspace_id": WORKSPACES.active_id, "forecast_id": version, "forecast_version": version,
                "forecast_input_id": version, "status": "completed", "valid_from": summary["window_start"],
                "valid_to": summary["window_end"], "generated_at": clock, "forecast_time": summary["window_start"], "boundary_flow": json.dumps(summary)}
    write_json(root / "boundary_flows/latest_forecast_input.json", {"summary": summary})
    write_json(root / "forecasts/latest.json", {"forecast_id": version})
    write_json(root / f"forecasts/{version}/forecast.json", metadata)
    (root / "forecasts/forecast_runs.jsonl").write_text(json.dumps(metadata)+"\n")
    latest = root / "forecasts/latest"
    latest.mkdir(exist_ok=True)
    steps = list(range(25))
    depths = np.zeros((25, len(mesh_rows)), dtype=np.float32)
    depths[1 if wet_now else 6:, 0] = 1.0
    depths[1 if wet_now else 6:, 2] = 1.0
    np.save(latest / "depth_series.npy", depths)
    (latest / "max_depth.csv").write_text("cell_id,max_depth\n1,1.0\n3,1.0\n")
    write_json(latest / "time_steps.json", {"time_steps_h": steps})
    WORKSPACES.update_manifest(status="paused", simulation_time=clock)
    runtime = context.event_runtime
    runtime._playback_paused = True
    runtime._playback_phase = "paused"
    policy = runtime._boundary_flow_runner.playback.policy
    policy.last_observation = {"simulation_time": clock, "observed_at": clock, "sequence": 1}
    policy.version = 2 if wet_now else 1
    policy.completed_forecast_version = policy.version
    runtime._append_output("runtime_status", {**runtime.status(), "label": "测试预测已就绪"})
    return metadata


scenarios = json.loads((Path(__file__).parent / "scenarios.json").read_text())


class ReplayAgent:
    def __init__(self):
        self.memory = {}
        self.harness = Harness(ontology=app.ontology, repository=app.repository, registry=app.registry,
                               llm_client=None, model="replay", config=HarnessConfig())
        configure_agent_query_tools(self.harness)
        configure_domain_tool_schemas(self.harness)
        register_map_tools(self.harness.tools, app.resolver, app.ontology)
        register_directive_tools(self.harness.tools, app.ontology)
        self.harness.hooks.register("post_tool_call", app.side_effects.capture_tool_event)

    def pending_tool_name(self, session_id):
        return None

    def chat_stream(self, message, session_id, allowed_tools=None, run_id="", trace_user_message=""):
        case = next(case for case in scenarios if case["message"] == trace_user_message)
        memory = self.memory.setdefault(session_id, {})
        def resolve(value):
            if isinstance(value, str) and value.startswith("$"):
                current = memory
                for field in value[1:].split("."):
                    current = current[int(field)] if isinstance(current, list) else current[field]
                return current
            if isinstance(value, dict): return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list): return [resolve(item) for item in value]
            return value
        for call in case["tools"]:
            name, parameters = call["name"], resolve(call["args"])
            if allowed_tools is not None and name not in allowed_tools:
                raise AssertionError(f"Product policy wrongly blocked {name}")
            yield ToolCallEvent(name=name, args=parameters)
            tool_result = self.harness.execute_tool(name, parameters, context=ToolUseContext(session_id=session_id, cache_namespace=run_id))
            result = tool_result.raw_content or tool_result.content
            parsed = json.loads(result)
            if "save" in call: memory[call["save"]] = parsed
            yield ToolResultEvent(name=name, result=result)
        yield TextEvent(content="本场景执行完毕，请查看结构化结果和地图。")


if not args.live:
    app.agent = ReplayAgent()
    app._chat_service = FloodChatService(app.agent, app.ontology, app.side_effects)
context = ApplicationContext(app=app, runs=AgentRunManager(app), event_runtime=EventRuntime(app), directives=DirectiveStore())


class TestHandler(Handler):
    def do_POST(self):
        if urlparse(self.path).path == "/__test__/forecast":
            body = self._read_json()
            return self._json(seed_forecast(bool(body.get("wet_now"))))
        return super().do_POST()

    def log_message(self, *values):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
server.app_context = context
write_json(args.ready_file, {"url": f"http://127.0.0.1:{server.server_port}", "workspace_id": WORKSPACES.active_id})
server.serve_forever()
