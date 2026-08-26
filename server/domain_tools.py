"""Read-only Domain OS tools and event references for OAG."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from domain_os import DomainQueryService, DomainRecordNotFound
from oag.tools.registry import ToolDef, ToolRegistry


DOMAIN_QUERY_TOOL_NAMES = frozenset({
    "domain_get_projection",
    "domain_list_products",
    "domain_get_product",
    "domain_list_commands",
    "domain_get_command",
    "domain_list_events",
})

_MAX_LIST_LIMIT = 50
_PRODUCT_ID_KEYS = frozenset({
    "product_id",
    "product_ids",
    "input_product_id",
    "input_product_ids",
    "forecast_product_id",
    "forecast_product_ids",
    "assessment_product_id",
    "assessment_product_ids",
    "input_refs",
})
_EVENT_ID_KEYS = frozenset({
    "event_id",
    "event_ids",
    "domain_event_id",
    "domain_event_ids",
})
_COMMAND_ID_KEYS = frozenset({
    "command_id",
    "command_ids",
    "causation_id",
})


QueryProvider = Callable[[], DomainQueryService]


def register_domain_query_tools(
    tools: ToolRegistry,
    query_provider: QueryProvider,
) -> None:
    """Register OAG adapters over the runtime-independent query boundary."""

    tools.register(ToolDef(
        name="domain_get_projection",
        description=(
            "读取 Domain OS 中由有效 Observation 形成的当前权威 Projection。"
            "Projection 表示现实状态，不包含预测或影响评估结果。"
        ),
        usage_prompt=(
            "已知 resource_id 时优先精确查询；不要把 DerivedProduct 中的预测值"
            "描述成 Projection 或已发生事实。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "具体 Resource ID，可选。",
                },
                "resource_type": {
                    "type": "string",
                    "description": "Resource 类型，可选。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _projection_result(queries, args),
        ),
        max_result_chars=12000,
    ))
    tools.register(ToolDef(
        name="domain_list_products",
        description=(
            "分页列出 Domain OS 的不可变 DerivedProduct，返回具体产品 ID、"
            "有效时间、输入引用和有界摘要。"
        ),
        usage_prompt=(
            "先按 product_type 或 subject_id 缩小范围，再用 domain_get_product"
            "读取选中的具体产品；下游分析必须保留具体 product_id，不使用 latest。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "product_type": {
                    "type": "string",
                    "description": "产品类型，例如 water.flood.forecast。",
                },
                "subject_id": {
                    "type": "string",
                    "description": "产品主体 Resource ID。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页起点，默认 0。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "description": "返回数量，默认 20，最大 50。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _products_result(queries, args),
        ),
        max_result_chars=16000,
    ))
    tools.register(ToolDef(
        name="domain_get_product",
        description=(
            "按具体 product_id 读取一个不可变 DerivedProduct，包含有效时间、"
            "血缘、结构化数据和 artifact 引用。大型集合会以计数和预览返回。"
        ),
        usage_prompt=(
            "product_id 必须来自事件或 domain_list_products 的明确结果；"
            "不要自行构造 ID，也不要用 latest 代替具体版本。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "完整且具体的 Domain OS 产品 ID。",
                },
            },
            "required": ["product_id"],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _product_result(queries, args),
        ),
        max_result_chars=20000,
    ))
    tools.register(ToolDef(
        name="domain_list_commands",
        description=(
            "分页查询 Domain OS 的受治理 Command 历史，包括原始 Intent、"
            "策略结论、审批信息、执行状态和输出引用。"
        ),
        usage_prompt=(
            "Command 状态是执行记录；acknowledged 只表示基础设施受理，"
            "不能描述成现实状态已生效，只有 confirmed 才表示协调完成。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": [
                        "pending_approval",
                        "rejected",
                        "dispatching",
                        "outcome_unknown",
                        "acknowledged",
                        "confirmed",
                        "failed",
                    ],
                    "description": "Command 状态，可选。",
                },
                "resource_id": {
                    "type": "string",
                    "description": "Intent 目标 Resource ID，可选。",
                },
                "actor_id": {
                    "type": "string",
                    "description": "提交 Intent 的 Actor ID，可选。",
                },
                "capability_id": {
                    "type": "string",
                    "description": "领域 Capability ID，可选。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "分页起点，默认 0。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "description": "返回数量，默认 20，最大 50。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _commands_result(queries, args),
        ),
        max_result_chars=16000,
    ))
    tools.register(ToolDef(
        name="domain_get_command",
        description=(
            "按具体 command_id 读取一个受治理 Command 及其 Intent、审批、"
            "状态、期望反馈和输出。"
        ),
        usage_prompt=(
            "必须区分 pending_approval、acknowledged 和 confirmed；"
            "outcome_unknown 不得猜测成功或失败。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "description": "完整且具体的 Domain OS Command ID。",
                },
            },
            "required": ["command_id"],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _command_result(queries, args),
        ),
        max_result_chars=12000,
    ))
    tools.register(ToolDef(
        name="domain_list_events",
        description=(
            "从 Domain OS 全局事件游标读取不可变事件，保留事件 ID、主体、"
            "产品引用以及 correlation/causation 字段。"
        ),
        usage_prompt=(
            "使用 after 游标增量读取；事件通知不代替产品本身，看到 product_id"
            "后用 domain_get_product 读取对应的不可变产品。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "after": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "一基全局游标；只返回该游标之后的事件。",
                },
                "event_type": {
                    "type": "string",
                    "description": "精确事件类型，可选。",
                },
                "subject_id": {
                    "type": "string",
                    "description": "精确主体 Resource ID，可选。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIST_LIMIT,
                    "description": "返回数量，默认 20，最大 50。",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=lambda args: _execute(
            query_provider,
            lambda queries: _events_result(queries, args),
        ),
        max_result_chars=16000,
    ))


def build_domain_event_context(
    queries: DomainQueryService,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve only concrete Domain OS IDs already carried by an event."""

    product_refs = []
    for product_id in _reference_values(event, _PRODUCT_ID_KEYS):
        try:
            product_refs.append(_product_reference(queries.product(product_id)))
        except (DomainRecordNotFound, ValueError):
            continue

    event_refs = []
    for event_id in _reference_values(event, _EVENT_ID_KEYS):
        try:
            event_refs.append(_event_reference(queries.event(event_id)))
        except (DomainRecordNotFound, ValueError):
            continue

    command_refs = []
    for command_id in _reference_values(event, _COMMAND_ID_KEYS):
        try:
            command_refs.append(_command_reference(queries.command(command_id)))
        except (DomainRecordNotFound, ValueError):
            continue

    linked = bool(product_refs or event_refs or command_refs)
    return {
        "domain_id": queries.domain_id,
        "access": "read_only",
        "linkage": "explicit" if linked else "unlinked_legacy_event",
        "product_refs": _unique_references(product_refs, "product_id"),
        "event_refs": _unique_references(event_refs, "event_id"),
        "command_refs": _unique_references(command_refs, "command_id"),
        "reference_policy": (
            "只使用本上下文中的具体 ID 继续查询，不用 latest 替代。"
            if linked
            else "当前事件未携带可验证的 Domain OS 引用，不得关联无关的最新产品或事件。"
        ),
    }


def _execute(
    query_provider: QueryProvider,
    operation: Callable[[DomainQueryService], dict[str, Any]],
) -> str:
    try:
        result = operation(query_provider())
    except Exception as exc:  # Tool adapters must return model-readable errors.
        result = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
    return json.dumps(result, ensure_ascii=False, default=str)


def _projection_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    result = queries.projections(
        resource_id=_optional_text(args.get("resource_id")),
        resource_type=_optional_text(args.get("resource_type")),
    )
    items = result["items"][:_MAX_LIST_LIMIT]
    return {
        **result,
        "items": [_compact_value(item, max_list_items=20) for item in items],
        "count": len(items),
        "truncated": len(result["items"]) > len(items),
    }


def _products_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    result = queries.products(
        product_type=_optional_text(args.get("product_type")),
        subject_id=_optional_text(args.get("subject_id")),
        offset=_nonnegative_int(args.get("offset", 0), "offset"),
        limit=_list_limit(args.get("limit", 20)),
    )
    return {
        **result,
        "items": [_product_reference(item, include_summary=True) for item in result["items"]],
    }


def _product_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    product = queries.product(str(args.get("product_id") or ""))
    return {
        **_product_reference(product),
        "data": _compact_value(product.get("data") or {}, max_list_items=30),
        "artifacts": _compact_value(product.get("artifacts") or {}, max_list_items=30),
    }


def _events_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    result = queries.events(
        after=_nonnegative_int(args.get("after", 0), "after"),
        event_type=_optional_text(args.get("event_type")),
        subject_id=_optional_text(args.get("subject_id")),
        limit=_list_limit(args.get("limit", 20)),
    )
    return {
        **result,
        "items": [
            {
                **item,
                "event": _compact_value(item["event"], max_list_items=30),
            }
            for item in result["items"]
        ],
    }


def _commands_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    result = queries.commands(
        state=_optional_text(args.get("state")),
        resource_id=_optional_text(args.get("resource_id")),
        actor_id=_optional_text(args.get("actor_id")),
        capability_id=_optional_text(args.get("capability_id")),
        offset=_nonnegative_int(args.get("offset", 0), "offset"),
        limit=_list_limit(args.get("limit", 20)),
    )
    return {
        **result,
        "items": [
            _compact_value(command, max_list_items=20)
            for command in result["items"]
        ],
    }


def _command_result(
    queries: DomainQueryService,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    return _compact_value(
        queries.command(str(args.get("command_id") or "")),
        max_list_items=30,
    )


def _product_reference(
    product: Mapping[str, Any],
    *,
    include_summary: bool = False,
) -> dict[str, Any]:
    result = {
        key: product.get(key)
        for key in (
            "product_id",
            "product_type",
            "subject_id",
            "producer_id",
            "generated_at",
            "valid_from",
            "valid_to",
            "input_refs",
            "correlation_id",
            "causation_id",
        )
    }
    result["artifact_names"] = sorted((product.get("artifacts") or {}).keys())
    if include_summary:
        result["data_summary"] = _compact_value(
            product.get("data") or {},
            max_list_items=3,
            max_depth=3,
            max_dict_items=30,
        )
    return result


def _event_reference(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in (
            "event_id",
            "event_type",
            "subject_id",
            "occurred_at",
            "correlation_id",
            "causation_id",
        )
    }


def _command_reference(command: Mapping[str, Any]) -> dict[str, Any]:
    intent = command.get("intent") or {}
    return {
        "command_id": command.get("command_id"),
        "state": command.get("state"),
        "resource_id": intent.get("resource_id"),
        "capability_id": intent.get("capability_id"),
        "actor_id": intent.get("actor_id"),
        "updated_at": command.get("updated_at"),
        "correlation_id": intent.get("correlation_id"),
    }


def _reference_values(value: Any, keys: frozenset[str]) -> list[str]:
    found: list[str] = []

    def visit(current: Any, key: str = "") -> None:
        if isinstance(current, Mapping):
            for child_key, child_value in current.items():
                visit(child_value, str(child_key))
            return
        if isinstance(current, (list, tuple)):
            for child in current:
                visit(child, key)
            return
        if key in keys and isinstance(current, str) and current.strip():
            found.append(current.strip())

    visit(value)
    return list(dict.fromkeys(found))


def _unique_references(
    values: list[dict[str, Any]],
    id_key: str,
) -> list[dict[str, Any]]:
    return list({str(value.get(id_key)): value for value in values}.values())


def _compact_value(
    value: Any,
    *,
    max_list_items: int,
    max_depth: int = 6,
    max_dict_items: int = 60,
    depth: int = 0,
) -> Any:
    if depth >= max_depth:
        if isinstance(value, Mapping):
            return {"_truncated": "object", "field_count": len(value)}
        if isinstance(value, (list, tuple)):
            return {"_truncated": "array", "item_count": len(value)}
    if isinstance(value, Mapping):
        items = list(value.items())
        compacted = {
            str(key): _compact_value(
                child,
                max_list_items=max_list_items,
                max_depth=max_depth,
                max_dict_items=max_dict_items,
                depth=depth + 1,
            )
            for key, child in items[:max_dict_items]
        }
        if len(items) > max_dict_items:
            compacted["_truncated_fields"] = len(items) - max_dict_items
        return compacted
    if isinstance(value, (list, tuple)):
        preview = [
            _compact_value(
                child,
                max_list_items=max_list_items,
                max_depth=max_depth,
                max_dict_items=max_dict_items,
                depth=depth + 1,
            )
            for child in value[:max_list_items]
        ]
        if len(value) <= max_list_items:
            return preview
        return {
            "_truncated": "array",
            "item_count": len(value),
            "preview": preview,
        }
    if isinstance(value, str) and len(value) > 2000:
        return {
            "_truncated": "string",
            "char_count": len(value),
            "preview": value[:2000],
        }
    return value


def _list_limit(value: Any) -> int:
    result = _nonnegative_int(value, "limit")
    if result < 1 or result > _MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST_LIMIT}")
    return result


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{label} must not be negative")
    return result


def _optional_text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None


__all__ = [
    "DOMAIN_QUERY_TOOL_NAMES",
    "build_domain_event_context",
    "register_domain_query_tools",
]
