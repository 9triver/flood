from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


PROJECT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from oag.runtime.events import event_to_dict  # noqa: E402
from server.agent_runs import AgentRunManager  # noqa: E402
from server.event_runtime import EventRuntime  # noqa: E402
from server.flood_app import FloodApp  # noqa: E402


APP = FloodApp()
RUNS = AgentRunManager(APP)
EVENT_RUNTIME = EventRuntime(APP)


class Handler(BaseHTTPRequestHandler):
    server_version = "FloodFrontend/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                return self._json(APP.bootstrap())
            if parsed.path == "/api/agent/chat/stream":
                return self._chat_stream(parsed.query)
            if parsed.path == "/api/autonomy/stream":
                return self._autonomy_stream(parsed.query)
            if parsed.path == "/api/autonomy/status":
                return self._json(EVENT_RUNTIME.status())
            if parsed.path == "/api/agent/runs/active":
                return self._active_run(parsed.query)
            if parsed.path == "/api/hydrodynamic-grid/meta":
                return self._hydrodynamic_grid_meta(parsed.query)
            if parsed.path == "/api/hydrodynamic-grid/tile":
                return self._hydrodynamic_grid_tile(parsed.query)
            if parsed.path == "/api/impact-analysis":
                return self._impact_analysis(parsed.query)
            if parsed.path == "/api/geojson":
                return self._geojson(parsed.query)
            if parsed.path == "/api/object":
                return self._object(parsed.query)
            return self._static(parsed.path)
        except Exception as exc:
            return self._json({"error": str(exc)}, status=500)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            target = STATIC_DIR / "index.html"
            if target.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(target.stat().st_size))
                self.end_headers()
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/autonomy/start":
                return self._json(EVENT_RUNTIME.start_playback(payload.get("speed_multiplier", 20)))
            if parsed.path == "/api/autonomy/stop":
                return self._json(EVENT_RUNTIME.stop_playback())
            if parsed.path == "/api/autonomy/pause":
                return self._json(EVENT_RUNTIME.pause_playback())
            if parsed.path == "/api/autonomy/resume":
                return self._json(EVENT_RUNTIME.resume_playback(payload.get("speed_multiplier", 1)))
            if parsed.path == "/api/autonomy/speed":
                return self._json(EVENT_RUNTIME.set_playback_speed(payload.get("speed_multiplier", 1)))
            if parsed.path == "/api/agent/confirm":
                return self._confirm(payload)
            if parsed.path == "/api/autonomy/reset":
                return self._json(EVENT_RUNTIME.restart_playback(payload.get("speed_multiplier", 20)))
            if parsed.path.startswith("/api/agent/runs/") and parsed.path.endswith("/cancel"):
                run_id = parsed.path.split("/")[-2]
                return self._json({"ok": RUNS.cancel(run_id), "run_id": run_id})
            return self._json({"error": "not found"}, status=404)
        except Exception as exc:
            return self._json({"error": str(exc)}, status=500)

    def _chat_stream(self, query: str):
        params = parse_qs(query)
        run_id = (params.get("run_id") or [""])[0]
        session_id = (params.get("session_id") or ["frontend-default"])[0]
        since = int((params.get("since") or ["0"])[0] or 0)

        if run_id:
            run = RUNS.get(run_id)
            if not run:
                return self._sse([
                    AgentRunManager._format_sse("text", {
                        "type": "text",
                        "content": "上一次生成任务已经不存在，请重新提问。",
                    }),
                    AgentRunManager._format_sse("done", {"type": "done"}),
                ])
            return self._sse(RUNS.stream(run, since))

        message = unquote((params.get("message") or [""])[0])
        selected_raw = (params.get("selected") or ["{}"])[0]
        try:
            selected = json.loads(unquote(selected_raw))
        except Exception:
            selected = {}
        if not message:
            return self._json({"error": "message is required"}, status=400)
        run = RUNS.start(session_id, message, selected)
        return self._sse(RUNS.stream(run, since=0))

    def _active_run(self, query: str):
        params = parse_qs(query)
        session_id = (params.get("session_id") or ["frontend-default"])[0]
        return self._json(RUNS.active_info(session_id))

    def _confirm(self, payload: dict):
        session_id = APP.agent_session_id(
            str(payload.get("session_id") or "frontend-default")
        )
        approved = bool(payload.get("approved"))
        answer = payload.get("answer")
        if not APP.agent or not APP.agent.has_pending(session_id):
            return self._json({"error": "no pending confirmation"}, status=400)

        def generator():
            try:
                for event in APP.agent.confirm_tool(session_id, approved, answer=answer):
                    data = event_to_dict(event)
                    yield AgentRunManager._format_sse(data["type"], data)
            except Exception as exc:
                yield AgentRunManager._format_sse("text", {
                    "type": "text",
                    "content": f"确认后继续执行失败：{exc}",
                })
            yield AgentRunManager._format_sse("done", {"type": "done"})

        return self._sse(generator())

    def _autonomy_stream(self, query: str):
        params = parse_qs(query)
        interval = max(5, int((params.get("interval") or ["5"])[0] or 5))
        return self._sse(EVENT_RUNTIME.stream(interval))

    def _geojson(self, query: str):
        params = parse_qs(query)
        object_type = (params.get("object_type") or [""])[0]
        if not object_type:
            return self._json({"error": "object_type is required"}, status=400)
        simplify = float((params.get("simplify_tolerance") or ["0"])[0] or 0)
        raw_filters = (params.get("filters") or ["{}"])[0]
        try:
            filters = json.loads(raw_filters) if raw_filters else {}
        except json.JSONDecodeError:
            return self._json({"error": "filters must be a JSON object"}, status=400)
        if not isinstance(filters, dict):
            return self._json({"error": "filters must be a JSON object"}, status=400)
        filters.update({
            key: _coerce_query_value(values[0])
            for key, values in params.items()
            if key not in {"object_type", "simplify_tolerance", "filters"} and values
        })
        _, body = APP.export_geojson(object_type, filters, simplify)
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _hydrodynamic_grid_tile(self, query: str):
        params = parse_qs(query)
        try:
            z = int((params.get("z") or [""])[0])
            x = int((params.get("x") or [""])[0])
            y = int((params.get("y") or [""])[0])
        except (TypeError, ValueError):
            return self._json({"error": "z, x and y are required integers"}, status=400)
        forecast_id = self._hydrodynamic_result_id(params)
        wet_only = str((params.get("wet_only") or [""])[0]).lower() in {"1", "true", "yes", "on"}
        time_h = _coerce_optional_float((params.get("time_h") or [""])[0])
        tile_crs = (params.get("tile_crs") or ["wgs84"])[0]
        data = APP.hydrodynamic_grid_tile(z, x, y, forecast_id, wet_only, time_h, tile_crs)
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _hydrodynamic_grid_meta(self, query: str):
        params = parse_qs(query)
        forecast_id = self._hydrodynamic_result_id(params)
        return self._json(APP.hydrodynamic_grid_stats(forecast_id))

    def _impact_analysis(self, query: str):
        params = parse_qs(query)
        result = APP.analyze_inundation_impacts(
            forecast_id=(params.get("forecast_id") or ["latest"])[0],
            target_type=(params.get("target_type") or ["all"])[0],
            min_depth_m=_coerce_float((params.get("min_depth_m") or ["0.15"])[0], 0.15),
            max_distance_m=_coerce_float((params.get("max_distance_m") or ["10"])[0], 10.0),
            time_h=_coerce_optional_float((params.get("time_h") or [""])[0]),
        )
        return self._json(result)

    def _hydrodynamic_result_id(self, params: dict[str, list[str]]) -> str:
        result = (params.get("result") or [""])[0]
        if result:
            return result
        return (params.get("forecast_id") or ["latest"])[0]

    def _object(self, query: str):
        params = parse_qs(query)
        object_type = (params.get("object_type") or [""])[0]
        object_id = (params.get("id") or [""])[0]
        if not object_type or not object_id:
            return self._json({"error": "object_type and id are required"}, status=400)
        return self._json(APP.get_object(object_type, object_id))

    def _static(self, path: str):
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        for chunk in chunks:
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except BrokenPipeError:
                break

    @staticmethod
    def _format_sse(event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def log_message(self, fmt: str, *args):
        print(f"{self.address_string()} - {fmt % args}")


def _coerce_query_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value


def _coerce_optional_float(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Flood server running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
