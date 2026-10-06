from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from oag.agent import Agent
from oag.harness import Harness
from oag.ontology.schema import Ontology
from oag.runtime import HarnessConfig
from oag.runtime.hooks import HookResult

from server.presentation.map_tools import register_map_tools
from server.presentation.directive_tools import register_directive_tools
from server.chat.analysis_context import normalize_analysis_tool


DEFAULT_AGENT_MAX_TURNS = 10
MAX_AGENT_MAX_TURNS = 20


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


def llm_enabled(config: dict[str, str]) -> bool:
    return bool(
        config.get("LLM_API_URL")
        and config.get("LLM_API_KEY")
        and config.get("LLM_MODEL")
    )


def configured_agent_max_turns(config: dict[str, str]) -> int:
    raw_value = config.get(
        "FLOOD_AGENT_MAX_TURNS", str(DEFAULT_AGENT_MAX_TURNS),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = DEFAULT_AGENT_MAX_TURNS
    return min(MAX_AGENT_MAX_TURNS, max(1, value))


def compact_agent_query_result(raw_result: str) -> str:
    try:
        payload = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        return raw_result

    def compact(value: Any) -> Any:
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "geometry":
                result["geometry_available"] = item not in (None, "", {})
                continue
            result[key] = compact(item)
        return result

    return json.dumps(compact(payload), ensure_ascii=False, default=str)


def validate_query_filters(ontology: Ontology, object_type: str, filters: Any) -> str | None:
    if filters is None:
        return None
    if not isinstance(filters, dict):
        return "filters must be an object"
    definition = ontology.objects.get(object_type)
    if definition is None:
        return f"unknown object_type: {object_type}"
    fields = set(definition.properties)
    if object_type in {"InundationForecastCell", "HydrodynamicGridCell"}:
        fields.update({"forecast_id", "time_h", "result"})
    for key in filters:
        field, _, operator = key.partition("__")
        if field not in fields or operator not in {"", "eq", "ne", "in", "like", "gt", "gte", "lt", "lte"}:
            return f"unsupported filter for {object_type}: {key}; query the object's declared fields first"
    return None


def configure_domain_tool_schemas(harness: Harness) -> None:
    for name, array_fields in (("find_nearby_objects", ["exclude_object_ids"]), ("analyze_inundation_impacts", ["object_ids"]), ("refine_object_set", ["exclude_object_ids"])):
        tool = harness.tools.get(name)
        if tool:
            for field in array_fields:
                tool.parameters["properties"][field]["items"] = {"type": "string"}
    nearby = harness.tools.get("find_nearby_objects")
    if nearby:
        nearby.max_result_chars = 32000
    for name in ("plan_route", "analyze_inundation_impacts", "get_flood_status", "compare_evacuation_sites", "review_route"):
        tool = harness.tools.get(name)
        if tool:
            tool.parameters["properties"]["view"]["enum"] = ["current", "time_slice", "envelope", "simulation_current"]
            tool.parameters["properties"]["view"]["description"] = (
                "对话中 current 默认使用本轮分析时刻（提问时地图选中帧，无时间轴则演进时刻）；"
                "time_slice 配合 time_h 指定时刻；simulation_current 明确使用演进时刻；"
                "envelope 为整个预测期最大包络。"
            )
    route = harness.tools.get("plan_route")
    if route:
        route.parameters["properties"]["profile"]["enum"] = ["car", "foot"]


def configure_agent_query_tools(harness: Harness) -> None:
    for tool_name in ("query", "count", "query_links", "search"):
        tool = harness.tools.get(tool_name)
        if not tool:
            continue
        original_handler = tool.handler
        def handler(args, original=original_handler, name=tool_name):
            if name in {"query", "count"}:
                error = validate_query_filters(harness.ontology, args.get("object_type", ""), args.get("filters"))
                if error:
                    return json.dumps({"error": error}, ensure_ascii=False)
            return compact_agent_query_result(original(args))
        tool.handler = handler
        tool.usage_prompt = (
            f"{tool.usage_prompt} 返回结果省略大型 geometry 坐标并用 "
            "geometry_available 标记；对象仍可通过 ui_* 地图工具按完整"
            "几何绘制，不要为了获取几何而重复查询。"
        ).strip()
        harness.tools.register(tool)


class FloodAgentFactory:
    def __init__(self, config: dict[str, str], project_dir: Path):
        self.config = config
        self.project_dir = project_dir

    @property
    def enabled(self) -> bool:
        return llm_enabled(self.config)

    def build_llm_client(self) -> OpenAI | None:
        if not self.enabled:
            return None
        api_url = self.config["LLM_API_URL"].rstrip("/")
        base_url = api_url.removesuffix("/chat/completions").removesuffix("/v1")
        return OpenAI(
            api_key=self.config["LLM_API_KEY"],
            base_url=f"{base_url}/v1",
            timeout=45,
        )

    def build_agent(
        self,
        *,
        llm_client: OpenAI | None,
        ontology: Ontology,
        repository: Any,
        registry: Any,
        resolver: Any,
        post_tool_call: Callable[[dict[str, Any]], HookResult],
    ) -> Agent | None:
        if not llm_client:
            return None
        harness = Harness(
            ontology=ontology,
            repository=repository,
            registry=registry,
            llm_client=llm_client,
            model=self.config["LLM_MODEL"],
            config=HarnessConfig(
                max_turns=configured_agent_max_turns(self.config),
                enable_write_confirmation=True,
                llm_extra_body=self._llm_extra_body(),
                genai_trace_json_path=str(self._genai_trace_json_path()),
                genai_trace_service_name="flood-emergency-agent",
                genai_trace_provider_name=self._genai_provider_name(),
                runtime_context={
                    "frontend": "GIS-centered flood emergency workspace",
                    "map_rendering": (
                        "Frontend renders domain objects by their geometry. "
                        "Layer is UI state, not a domain object."
                    ),
                },
            ),
        )
        configure_agent_query_tools(harness)
        configure_domain_tool_schemas(harness)
        register_map_tools(harness.tools, resolver, ontology)
        register_directive_tools(harness.tools, ontology)
        harness.hooks.register("pre_tool_call", normalize_analysis_tool)
        harness.hooks.register("post_tool_call", post_tool_call)
        return Agent(
            harness,
            llm_client,
            self.config["LLM_MODEL"],
            db_dir=str(self.config.get("OAG_DATA_DIR") or self.project_dir / ".oag_data"),
        )

    def _llm_extra_body(self) -> dict[str, Any]:
        disabled = str(
            self.config.get("LLM_DISABLE_REASONING", "")
        ).lower() in {"1", "true", "yes", "on"}
        return {"enable_thinking": False} if disabled else {}

    def _genai_provider_name(self) -> str:
        configured = self.config.get("GENAI_TRACE_PROVIDER")
        if configured:
            return configured
        api_url = self.config.get("LLM_API_URL", "").lower()
        if "deepseek" in api_url:
            return "deepseek"
        if "openai" in api_url:
            return "openai"
        return "openai"

    def _genai_trace_json_path(self) -> Path:
        configured = self.config.get("GENAI_TRACE_JSON_PATH")
        if configured:
            path = Path(configured).expanduser()
            return path if path.is_absolute() else self.project_dir / path
        return self.project_dir / ".oag_data" / "genai_traces_flood.json"
