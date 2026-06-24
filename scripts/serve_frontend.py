from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


PROJECT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_DIR / "frontend"
DOMAIN_DIR = PROJECT_DIR / "domains" / "flood"

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "agent"))

from frontend_server.map_planner import MapActionPlanner  # noqa: E402
from frontend_server.map_tools import register_map_tools, tool_result_to_map_event  # noqa: E402
from openai import OpenAI  # noqa: E402
from oag.agent import Agent  # noqa: E402
from oag.harness import Harness  # noqa: E402
from oag.ontology.loader import load_domain  # noqa: E402
from oag.runtime import HarnessConfig  # noqa: E402
from oag.runtime.events import event_to_dict  # noqa: E402
from oag.runtime.hooks import HookResult  # noqa: E402


ID_FIELDS = {
    "River": "river_id",
    "Watershed": "watershed_id",
    "Waterway": "waterway_id",
    "County": "county_id",
    "Town": "town_id",
    "Reservoir": "reservoir_id",
    "Sluice": "sluice_id",
    "HydraulicStructure": "structure_id",
    "Road": "road_id",
    "Bridge": "bridge_id",
    "Facility": "facility_id",
    "Place": "place_id",
    "Transfer": "transfer_id",
    "Route": "route_id",
    "Scenario": "scenario_id",
    "Cell": "cell_id",
}

def load_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


class FloodApp:
    def __init__(self):
        self.llm_config = load_env(PROJECT_DIR / ".env")
        self.llm_client = self._build_llm_client()
        self.ontology, self.repository, self.registry = load_domain(DOMAIN_DIR)
        self.resolver = self.registry.get_resolver("flood_repository")
        self.scenarios = self.registry.call("list_scenarios")
        self.map_planner = MapActionPlanner(self.resolver, self.registry, self.scenarios)
        self._pending_map_events: dict[str, list[dict[str, Any]]] = {}
        self._pending_map_events_lock = threading.Lock()
        self.agent = self._build_agent()
        self._export_lock = threading.Lock()

    @property
    def llm_enabled(self) -> bool:
        return bool(
            self.llm_config.get("LLM_API_URL")
            and self.llm_config.get("LLM_API_KEY")
            and self.llm_config.get("LLM_MODEL")
        )

    def bootstrap(self) -> dict:
        mappable = self.registry.call("list_mappable_objects")
        return {
            "domain": self.ontology.name,
            "title": "珊瑚河洪水应急预警智能体",
            "mappable": mappable,
            "counts": {
                "school": self.resolver.count("Facility", {"facility_type": "school"}),
                "hospital": self.resolver.count("Facility", {"facility_type": "hospital"}),
                "government": self.resolver.count("Facility", {"facility_type": "government"}),
            },
            "llm_enabled": self.llm_enabled,
            "default_context": "基础态 · 领域对象地图",
        }

    def export_geojson(self, object_type: str, filters: dict,
                       simplify: float = 0) -> tuple[dict, bytes]:
        with self._export_lock:
            result = self.registry.call(
                "export_objects_geojson",
                object_type=object_type,
                filters=filters,
                simplify_tolerance=simplify,
                force=False,
            )
            if "error" in result:
                raise ValueError(result["error"])
            path = Path(result["absolute_path"])
            return result, path.read_bytes()

    def get_object(self, object_type: str, object_id: str) -> dict:
        row = self.resolver.query_by_id(object_type, object_id)
        if row:
            return {"object_type": object_type, "object": row}
        id_field = ID_FIELDS.get(object_type)
        rows = self.resolver.query(object_type, {id_field: object_id}, limit=1) if id_field else []
        return {"object_type": object_type, "object": rows[0] if rows else None}

    def stream_chat(self, run: "AgentRun"):
        selected = run.selected or {}

        if not self.agent:
            plan = self.map_planner.plan(run.message, selected)
            self._append_map_event(run, plan)
            self._append_event(run, "text", {
                "type": "text",
                "content": plan.get("note") or "当前未启用 LLM，已根据地图动作规则更新地图。",
            })
            return

        agent_message = self._agent_message(run.message, selected)
        emitted_map_action = False
        try:
            for event in self.agent.chat_stream(agent_message, session_id=run.session_id):
                if run.cancelled:
                    break
                for map_event in self._pop_pending_map_events(run.session_id):
                    emitted_map_action = True
                    self._append_map_event(run, map_event)
                data = event_to_dict(event)
                self._append_event(run, data["type"], data)
            for map_event in self._pop_pending_map_events(run.session_id):
                emitted_map_action = True
                self._append_map_event(run, map_event)
        except Exception as exc:
            print(f"OAG agent stream failed: {exc}")
            self._append_event(run, "text", {
                "type": "text",
                "content": f"智能体生成失败：{exc}",
            })
        if not emitted_map_action:
            plan = self.map_planner.plan(run.message, selected)
            if plan.get("map_actions"):
                self._append_map_event(run, plan)

    def _append_map_event(self, run: "AgentRun", result: dict):
        self._append_event(run, "map_actions", {
            "type": "map_actions",
            "context": result.get("context"),
            "map_actions": result.get("map_actions", []),
            "result_cards": result.get("result_cards", []),
            "llm_enabled": bool(self.agent),
        })

    @staticmethod
    def _append_event(run: "AgentRun", event_type: str, data: dict[str, Any]):
        with run.condition:
            run.seq += 1
            item = {
                "seq": run.seq,
                "type": event_type,
                "data": {**data, "seq": run.seq, "run_id": run.run_id},
            }
            run.events.append(item)
            run.updated_at = time.time()
            run.condition.notify_all()

    def _build_llm_client(self) -> OpenAI | None:
        if not self.llm_enabled:
            return None
        api_url = self.llm_config["LLM_API_URL"].rstrip("/")
        base_url = api_url.removesuffix("/chat/completions").removesuffix("/v1")
        return OpenAI(
            api_key=self.llm_config["LLM_API_KEY"],
            base_url=f"{base_url}/v1",
            timeout=45,
        )

    def _build_agent(self) -> Agent | None:
        if not self.llm_client:
            return None
        harness = Harness(
            ontology=self.ontology,
            repository=self.repository,
            registry=self.registry,
            llm_client=self.llm_client,
            model=self.llm_config["LLM_MODEL"],
            config=HarnessConfig(
                max_turns=6,
                enable_write_confirmation=True,
                runtime_context={
                    "frontend": "GIS-centered flood emergency workspace",
                    "map_rendering": "Frontend renders domain objects by their geometry. Layer is UI state, not a domain object.",
                },
                append_system_prompt=(
                    "你服务于一个以珊瑚河流域 GIS 为中心的前端。"
                    "回答用户时优先调用领域对象查询和领域函数获取事实。"
                    "不要让用户通过情景切换控件操作；如果需要其他情景，请让用户直接在对话中指定。"
                    "当用户要求在地图上显示、打开、绘制、加载、叠加、缩放、聚焦或清空对象时，"
                    "必须调用 ui_show_objects、ui_focus_object 或 ui_clear_map，让前端执行地图动作；"
                    "不要只用文字说明将要显示什么。"
                    "当前 analyze_risks/plan_response/generate_brief 可能尚未实现；如工具返回 not_implemented，必须如实说明。"
                    "在空间叠加风险函数实现前，不得声称已经判定某个学校、道路、桥梁或路线受淹；只能说明已展示对象和淹没范围，可作为研判基础。"
                ),
            ),
        )
        register_map_tools(harness.tools, self.resolver, self.registry)
        harness.hooks.register("post_tool_call", self._capture_map_tool_event)
        return Agent(
            harness,
            self.llm_client,
            self.llm_config["LLM_MODEL"],
            db_dir=str(PROJECT_DIR / ".oag_data"),
        )

    def _agent_message(self, message: str, selected: dict) -> str:
        frontend_context = {
            "用户问题": message,
            "选中对象": selected,
            "地图动作工具": ["ui_show_objects", "ui_clear_map", "ui_focus_object"],
        }
        return (
            f"用户问题：{message}\n\n"
            "以下是前端 GIS 的当前状态，只用于帮助你理解用户正在看的地图。"
            "不要把这些前端动作当作领域事实；领域事实必须通过 OAG 工具查询。"
            "如果用户请求地图展示，请调用 ui_* 工具；这些工具只改变前端显示，不改变领域数据。"
            "如果 analyze_risks 返回 not_implemented，不要声称已经完成对象级受淹判定。\n"
            f"{json.dumps(frontend_context, ensure_ascii=False, indent=2)}"
        )

    def _capture_map_tool_event(self, context: dict[str, Any]) -> HookResult:
        event = tool_result_to_map_event(
            str(context.get("tool_name") or ""),
            str(context.get("result") or ""),
        )
        if not event:
            return HookResult(action="allow")
        session_id = str(context.get("session_id") or "")
        if session_id:
            with self._pending_map_events_lock:
                self._pending_map_events.setdefault(session_id, []).append(event)
        return HookResult(action="allow")

    def _pop_pending_map_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._pending_map_events_lock:
            return self._pending_map_events.pop(session_id, [])


APP = FloodApp()


class AgentRun:
    def __init__(self, run_id: str, session_id: str, message: str,
                 selected: dict | None = None):
        self.run_id = run_id
        self.session_id = session_id
        self.message = message
        self.selected = selected or {}
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.cancelled = False
        self.seq = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.condition = threading.Condition()


class AgentRunManager:
    def __init__(self, app: FloodApp):
        self.app = app
        self._runs: dict[str, AgentRun] = {}
        self._active_by_session: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, session_id: str, message: str,
              selected: dict | None = None) -> AgentRun:
        run = AgentRun(uuid.uuid4().hex, session_id, message, selected)
        with self._lock:
            self._runs[run.run_id] = run
            self._active_by_session[session_id] = run.run_id
        thread = threading.Thread(target=self._execute, args=(run,), daemon=True)
        thread.start()
        return run

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active(self, session_id: str) -> AgentRun | None:
        with self._lock:
            run_id = self._active_by_session.get(session_id)
            run = self._runs.get(run_id) if run_id else None
        if not run:
            return None
        with run.condition:
            return None if run.done or run.cancelled else run

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if not run:
            return False
        with run.condition:
            run.cancelled = True
            run.condition.notify_all()
        return True

    def stream(self, run: AgentRun, since: int = 0):
        yield self._format_sse("run", {
            "type": "run",
            "run_id": run.run_id,
            "session_id": run.session_id,
            "done": run.done,
            "seq": run.seq,
        })

        next_seq = max(1, int(since or 0) + 1)
        while True:
            pending = []
            done = False
            should_ping = False
            with run.condition:
                while not run.done and not run.cancelled and run.seq < next_seq:
                    run.condition.wait(timeout=15)
                    if run.seq < next_seq:
                        should_ping = True
                        break
                pending = [event for event in run.events if int(event.get("seq", 0)) >= next_seq]
                done = run.done or run.cancelled
            if should_ping:
                yield self._format_sse("ping", {"type": "ping"})
                continue
            for event in pending:
                next_seq = int(event["seq"]) + 1
                yield self._format_sse(event["type"], event["data"])
            if done and not pending:
                break

    def active_info(self, session_id: str) -> dict:
        run = self.get_active(session_id)
        if not run:
            return {"run_id": None}
        with run.condition:
            return {
                "run_id": run.run_id,
                "session_id": run.session_id,
                "seq": run.seq,
                "done": run.done,
                "cancelled": run.cancelled,
            }

    def _execute(self, run: AgentRun):
        try:
            self.app.stream_chat(run)
        finally:
            self.app._append_event(run, "done", {"type": "done"})
            with run.condition:
                run.done = True
                run.updated_at = time.time()
                run.condition.notify_all()
            with self._lock:
                if self._active_by_session.get(run.session_id) == run.run_id:
                    self._active_by_session.pop(run.session_id, None)

    @staticmethod
    def _format_sse(event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


RUNS = AgentRunManager(APP)


class Handler(BaseHTTPRequestHandler):
    server_version = "FloodFrontend/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                return self._json(APP.bootstrap())
            if parsed.path == "/api/agent/chat/stream":
                return self._chat_stream(parsed.query)
            if parsed.path == "/api/agent/runs/active":
                return self._active_run(parsed.query)
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
            target = FRONTEND_DIR / "index.html"
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
            if parsed.path == "/api/agent/confirm":
                return self._confirm(payload)
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
        session_id = str(payload.get("session_id") or "frontend-default")
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
            key: values[0]
            for key, values in params.items()
            if key not in {"object_type", "simplify_tolerance", "filters"} and values
        })
        _, body = APP.export_geojson(object_type, filters, simplify)
        self.send_response(200)
        self.send_header("Content-Type", "application/geo+json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _object(self, query: str):
        params = parse_qs(query)
        object_type = (params.get("object_type") or [""])[0]
        object_id = (params.get("id") or [""])[0]
        if not object_type or not object_id:
            return self._json({"error": "object_type and id are required"}, status=400)
        return self._json(APP.get_object(object_type, object_id))

    def _static(self, path: str):
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (FRONTEND_DIR / rel).resolve()
        if not str(target).startswith(str(FRONTEND_DIR.resolve())) or not target.exists():
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

    def log_message(self, fmt: str, *args):
        print(f"{self.address_string()} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Flood frontend running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
