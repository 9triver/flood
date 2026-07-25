from __future__ import annotations

import json
from typing import Any

from oag.ontology.schema import Ontology, PresentationToolDef
from oag.tools.registry import ToolDef, ToolPolicy, ToolRegistry

from domains.flood.runtime.common import MAPPABLE_OBJECTS
from server.presentation.map_actions import MapActionBuilder


def _object_alias_prompt(ontology: Ontology) -> str:
    mappings = []
    for object_type in sorted(MAPPABLE_OBJECTS):
        object_def = ontology.objects.get(object_type)
        if not object_def:
            continue
        for alias in object_def.aliases:
            if not alias.terms:
                continue
            mapping = f"{'/'.join(alias.terms)} => {object_type}"
            if alias.filters:
                mapping += f" filters={json.dumps(alias.filters, ensure_ascii=False)}"
            mappings.append(mapping)
    return "；".join(mappings)


def _presentation_tool(ontology: Ontology, name: str) -> PresentationToolDef:
    tool = ontology.presentation_tools.get(name)
    if not tool:
        raise ValueError(f"ontology presentation tool not found: {name}")
    return tool


def _presentation_object_types(ontology: Ontology,
                               tool: PresentationToolDef) -> list[str]:
    if tool.object_scope == "mappable":
        return sorted(set(MAPPABLE_OBJECTS) & set(ontology.objects))
    if tool.object_scope == "listed":
        return sorted(set(tool.allowed_objects) & set(MAPPABLE_OBJECTS))
    return []


def _presentation_tool_kwargs(ontology: Ontology, name: str,
                              **prompt_context: str) -> dict[str, Any]:
    definition = _presentation_tool(ontology, name)
    usage_prompt = definition.usage_prompt
    for key, value in prompt_context.items():
        usage_prompt = usage_prompt.replace("{" + key + "}", value)
    return {
        "description": definition.description or definition.summary,
        "usage_prompt": usage_prompt,
        "category": definition.category,
        "policy": ToolPolicy(
            read_only=definition.side_effect_scope == "none",
            requires_confirmation=definition.requires_confirmation,
            concurrency_safe=definition.concurrency_safe,
            worker_allowed=definition.worker_allowed,
            idempotent=definition.idempotent,
            destructive=definition.destructive,
            timeout_seconds=definition.timeout_seconds,
        ),
    }


def register_map_tools(tools: ToolRegistry, resolver,
                       ontology: Ontology) -> None:
    """Register frontend orchestration tools.

    These tools do not mutate domain data. They return declarative UI actions
    that the frontend service translates into SSE map_actions events.
    """

    action_builder = MapActionBuilder(ontology=ontology, resolver=resolver)
    show_objects_def = _presentation_tool(ontology, "ui_show_objects")
    object_types = _presentation_object_types(ontology, show_objects_def)
    show_objects_metadata = _presentation_tool_kwargs(
        ontology,
        "ui_show_objects",
        object_aliases=_object_alias_prompt(ontology),
    )

    tools.register(ToolDef(
        name="ui_show_objects",
        parameters={
            "type": "object",
            "properties": {
                "objects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "object_type": {"type": "string", "enum": object_types},
                            "filters": {"type": "object", "description": "对象过滤条件，例如学校为 {\"facility_type\":\"school\"}"},
                            "label": {"type": "string", "description": "地图图层显示名称，可选"},
                            "fit": {"type": "boolean", "description": "是否缩放到该对象范围"},
                            "refresh": {"type": "boolean", "description": "是否刷新已有图层"},
                            "object_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "需要显示或高亮的对象 ID 列表，可选；show_only_object_ids=true 时只显示这些对象",
                            },
                            "highlight": {"type": "boolean", "description": "是否高亮 object_ids 指定对象"},
                            "show_only_object_ids": {"type": "boolean", "description": "是否只加载 object_ids 指定对象，适合影响分析结果"},
                            "simplify_tolerance": {"type": "number", "description": "大型面对象简化容差"},
                        },
                        "required": ["object_type"],
                    },
                    "description": "要显示的领域对象列表。",
                },
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": ["objects"],
        },
        handler=lambda args: action_builder.show_objects(args, object_types),
        max_result_chars=8000,
        **show_objects_metadata,
    ))

    tools.register(ToolDef(
        name="ui_clear_map",
        parameters={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["map", "inundation"],
                    "description": "map 表示重置地图；inundation 表示只清除淹没范围/水动力结果，不改变地图视野",
                },
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": [],
        },
        handler=action_builder.clear_map,
        max_result_chars=2000,
        **_presentation_tool_kwargs(ontology, "ui_clear_map"),
    ))

    focus_def = _presentation_tool(ontology, "ui_focus_object")
    focus_object_types = _presentation_object_types(ontology, focus_def)
    tools.register(ToolDef(
        name="ui_focus_object",
        parameters={
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "enum": focus_object_types,
                    "description": "选中对象类型，可选",
                },
                "object_id": {"type": "string", "description": "选中对象 ID，可选"},
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
            },
            "required": [],
        },
        handler=lambda args: action_builder.focus_object(args, focus_object_types),
        max_result_chars=2000,
        **_presentation_tool_kwargs(ontology, "ui_focus_object"),
    ))

    event_marker_def = _presentation_tool(ontology, "ui_show_event_marker")
    event_source_types = _presentation_object_types(ontology, event_marker_def)
    tools.register(ToolDef(
        name="ui_show_event_marker",
        parameters={
            "type": "object",
            "properties": {
                "event": {
                    "type": "object",
                    "description": "领域事件对象，必须包含 event_id/event_type/title/longitude/latitude/payload 等可用字段。",
                },
                "context": {"type": "string", "description": "地图上下文短标题"},
                "note": {"type": "string", "description": "给用户的简短说明"},
                "fit": {"type": "boolean", "description": "是否动画缩放到该事件 marker"},
                "show_source": {"type": "boolean", "description": "是否同时加载事件来源对象，例如水文测站"},
            },
            "required": ["event"],
        },
        handler=lambda args: action_builder.show_event_marker(args, event_source_types),
        max_result_chars=4000,
        **_presentation_tool_kwargs(ontology, "ui_show_event_marker"),
    ))
