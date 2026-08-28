#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_PATH = PROJECT_DIR / ".oag_data" / "genai_traces_flood.json"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "local" / "evaluation-submission"
DEFAULT_ENDPOINT = "http://127.0.0.1:8765/v1/chat/completions"


class SubmissionBuildError(Exception):
    pass


def main() -> int:
    env_values = load_dotenv(PROJECT_DIR / ".env")
    default_model = os.environ.get("LLM_MODEL") or env_values.get("LLM_MODEL") or "flood-emergency-agent"
    parser = argparse.ArgumentParser(
        description="Build evaluation submission files from OAG GenAI OTLP traces.",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=DEFAULT_TRACE_PATH,
        help="Source OTLP trace JSON. Defaults to .oag_data/genai_traces_flood.json.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory. Defaults to local/evaluation-submission.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("FLOOD_EVALUATION_ENDPOINT", DEFAULT_ENDPOINT),
        help="OpenAI-compatible dynamic evaluation endpoint written to information.json.",
    )
    parser.add_argument(
        "--model",
        default=default_model,
        help="Model name written to information.json.",
    )
    parser.add_argument(
        "--dynamic-report",
        type=Path,
        help="Optional execution-report.json from dynamic-evaluation-runner; enables traces-dynamic.json generation.",
    )
    parser.add_argument(
        "--refresh-static",
        action="store_true",
        help="Overwrite an existing traces.json from --trace. By default it is preserved.",
    )
    args = parser.parse_args()

    try:
        build_submission(
            trace_path=args.trace,
            output_dir=args.out,
            endpoint=args.endpoint,
            model=args.model,
            dynamic_report_path=args.dynamic_report,
            refresh_static=args.refresh_static,
        )
        return 0
    except SubmissionBuildError as exc:
        print(f"error: {exc}")
        return 1


def build_submission(
    *,
    trace_path: Path,
    output_dir: Path,
    endpoint: str,
    model: str,
    dynamic_report_path: Path | None = None,
    refresh_static: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = read_json(trace_path)
    dynamic_trace_ids = trace_ids_from_dynamic_report(dynamic_report_path) if dynamic_report_path else set()

    traces_path = output_dir / "traces.json"
    if refresh_static or not traces_path.exists():
        static_source = filter_otlp_by_trace_ids(source, exclude_trace_ids=dynamic_trace_ids)
        ensure_has_spans(static_source, "static traces")
        write_json(traces_path, static_source)
        traces_action = "wrote"
    else:
        traces_action = "preserved"
    static_source = read_json(traces_path)
    static_hash = sha256_file(traces_path)

    information = build_information(
        static_source,
        endpoint=endpoint,
        model=model,
        traces_hash=static_hash,
    )
    write_json(output_dir / "information.json", information)

    print(f"{traces_action} {traces_path}")
    print(f"wrote {output_dir / 'information.json'}")

    if dynamic_report_path:
        if not dynamic_trace_ids:
            raise SubmissionBuildError("dynamic report does not contain any trace_id values")
        dynamic_source = filter_otlp_by_trace_ids(source, include_trace_ids=dynamic_trace_ids)
        ensure_has_spans(dynamic_source, "dynamic traces")
        found = trace_ids_in_document(dynamic_source)
        missing = sorted(dynamic_trace_ids - found)
        if missing:
            raise SubmissionBuildError(
                "dynamic trace ids are not present in source trace: " + ", ".join(missing),
            )
        write_json(output_dir / "traces-dynamic.json", dynamic_source)
        print(f"wrote {output_dir / 'traces-dynamic.json'}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SubmissionBuildError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SubmissionBuildError(f"invalid JSON in {path}: {exc.msg}") from exc


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def filter_otlp_by_trace_ids(
    document: Any,
    *,
    include_trace_ids: set[str] | None = None,
    exclude_trace_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        raise SubmissionBuildError("source trace must be standard OTLP JSON with resourceSpans")
    include_trace_ids = {item.lower() for item in include_trace_ids or set()}
    exclude_trace_ids = {item.lower() for item in exclude_trace_ids or set()}

    output = {key: deepcopy(value) for key, value in document.items() if key != "resourceSpans"}
    output["resourceSpans"] = []
    for resource_span in document.get("resourceSpans", []):
        if not isinstance(resource_span, dict):
            continue
        resource_copy = {key: deepcopy(value) for key, value in resource_span.items() if key != "scopeSpans"}
        resource_copy["scopeSpans"] = []
        for scope_span in resource_span.get("scopeSpans", []):
            if not isinstance(scope_span, dict):
                continue
            kept_spans = []
            for span in scope_span.get("spans", []):
                trace_id = str(span.get("traceId") or span.get("trace_id") or "").lower()
                if include_trace_ids and trace_id not in include_trace_ids:
                    continue
                if exclude_trace_ids and trace_id in exclude_trace_ids:
                    continue
                kept_spans.append(deepcopy(span))
            if kept_spans:
                scope_copy = {key: deepcopy(value) for key, value in scope_span.items() if key != "spans"}
                scope_copy["spans"] = kept_spans
                resource_copy["scopeSpans"].append(scope_copy)
        if resource_copy["scopeSpans"]:
            output["resourceSpans"].append(resource_copy)
    return output


def ensure_has_spans(document: dict[str, Any], label: str) -> None:
    if not iter_spans(document):
        raise SubmissionBuildError(f"{label} contains no spans")


def trace_ids_from_dynamic_report(path: Path | None) -> set[str]:
    if not path:
        return set()
    report = read_json(path)
    tests = report.get("tests") if isinstance(report, dict) else None
    if not isinstance(tests, list):
        raise SubmissionBuildError(f"dynamic report has no tests array: {path}")
    trace_ids = set()
    missing: list[str] = []
    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            continue
        trace_id = test.get("trace_id")
        if isinstance(trace_id, str) and len(trace_id) == 32:
            trace_ids.add(trace_id.lower())
        else:
            missing.append(str(test.get("test_id") or index))
    if missing:
        raise SubmissionBuildError(
            "dynamic report tests missing trace_id: " + ", ".join(missing),
        )
    return trace_ids


def trace_ids_in_document(document: dict[str, Any]) -> set[str]:
    return {span.trace_id.lower() for span in iter_spans(document)}


def build_information(
    traces: dict[str, Any],
    *,
    endpoint: str,
    model: str,
    traces_hash: str,
) -> dict[str, Any]:
    evidence = choose_evidence_trace(traces)
    return {
        "business_intent_examples": [
            {
                "case_id": "C1-flood-emergency-001",
                "instruction": "基于珊瑚河当前演进与预测结果，判断需要优先处置的受影响对象，并给出应急处置建议。",
                "expected_result": {
                    "required_fields": [
                        "risk_summary",
                        "affected_objects",
                        "recommended_actions",
                        "evidence",
                    ],
                    "expected_values": {
                        "risk_summary": "说明洪水演进、预测时刻和关键风险等级，不把前端地图状态当成领域事实。",
                        "affected_objects": "列出道路、桥梁、居民点或安置点等受淹/受阻对象及其风险原因。",
                        "recommended_actions": "给出交通管制、群众转移、路线规划或应急指令等可执行建议。",
                        "evidence": "引用水动力预测、淹没影响分析、对象查询或路线规划工具返回的关键事实。",
                    },
                },
            }
        ],
        "inference_task_examples": [
            {
                "case_id": "C2-flood-impact-001",
                "instruction": "根据预测淹没水深和对象空间关系，判断珊瑚河桥梁、道路和居民点是否受影响，并推导转移处置优先级。",
                "scenario_facts": {
                    "forecast_source": "CNN 水动力预测结果提供各预测时刻的淹没深度和范围。",
                    "impact_objects": "领域对象库包含道路、桥梁、居民点、安置点、水库和水文站等对象及几何位置。",
                    "bridge_condition": "桥梁两侧桥头若被明显淹没，应按通行高风险处理。",
                    "route_condition": "避洪路线规划需要避开超过阻断水深阈值的路段。",
                },
                "business_rules": [
                    {
                        "rule_id": "R1",
                        "condition": "对象位置与预测淹没区域相交，且邻近最大水深达到业务阈值。",
                        "conclusion": "对象应标记为受影响，并进入应急研判摘要。",
                    },
                    {
                        "rule_id": "R2",
                        "condition": "桥梁两侧桥头均存在明显淹没或邻近高水深。",
                        "conclusion": "桥梁通行能力判定为高风险，应建议绕行或交通管制。",
                    },
                    {
                        "rule_id": "R3",
                        "condition": "居民点受淹且存在可达安置点。",
                        "conclusion": "需要规划避洪路线并形成转移组织建议。",
                    },
                ],
                "expected_inference": {
                    "required_facts": [
                        "forecast_source",
                        "impact_objects",
                        "bridge_condition",
                        "route_condition",
                    ],
                    "applicable_rules": ["R1", "R2", "R3"],
                    "expected_conclusion": "智能体应基于预测结果和领域规则识别受影响对象，说明风险原因，并给出转移、绕行或应急指令建议。",
                },
            }
        ],
        "task_log_evidence_examples": [build_task_log_evidence(evidence, traces_hash)],
        "tool_skill_examples": build_tool_skill_examples(),
        "memory_capability_examples": [
            {
                "memory_name": "会话上下文记忆",
                "memory_description": "系统按 gen_ai.conversation.id 维持同一业务会话中的用户问题、模型回答和工具结果，使后续提问能够引用前序洪水演进、影响分析或路线规划上下文。",
                "memory_source_field_paths": [
                    "gen_ai.input.messages",
                    "gen_ai.conversation.id",
                ],
                "memory_use_field_paths": [
                    "gen_ai.input.messages",
                    "gen_ai.output.messages",
                    "gen_ai.tool.call.arguments",
                ],
                "memory_link_field_paths": [
                    "gen_ai.conversation.id",
                    "oag.session.id",
                ],
                "success_description": "同一会话的后续请求无需重复全部背景，智能体仍可从历史消息和工具结果中延续已有研判。",
            }
        ],
        "api": {
            "protocol": "openai_chat_completions",
            "endpoint": endpoint,
            "model": model,
            "authentication": {
                "method": "none",
            },
            "request_template": {
                "model": "{model}",
                "messages": [
                    {
                        "role": "user",
                        "content": "{instruction}",
                    }
                ],
            },
            "response_mapping": {
                "output_text_path": "choices.0.message.content",
            },
            "trace_mapping": {
                "trace_id_header": "traceparent",
                "trace_id_response_path": "trace_id",
            },
        },
    }


def build_task_log_evidence(evidence: "EvidenceTrace", traces_hash: str) -> dict[str, Any]:
    stages = []
    if evidence.root_span_id:
        stages.append({
            "stage_name": "接收任务并建立智能体运行",
            "stage_evidence_span_ids": [evidence.root_span_id],
        })
    if evidence.chat_span_ids:
        stages.append({
            "stage_name": "调用大模型理解任务并规划工具",
            "stage_evidence_span_ids": [evidence.chat_span_ids[0]],
        })
    if evidence.tool_span_ids:
        stages.append({
            "stage_name": "调用领域工具获取水利和交通证据",
            "stage_evidence_span_ids": evidence.tool_span_ids[:6],
        })
    final_stage_spans = []
    if evidence.chat_span_ids:
        final_stage_spans.append(evidence.chat_span_ids[-1])
    if evidence.root_span_id and evidence.root_span_id not in final_stage_spans:
        final_stage_spans.append(evidence.root_span_id)
    if final_stage_spans:
        stages.append({
            "stage_name": "综合工具结果生成业务结论",
            "stage_evidence_span_ids": final_stage_spans,
        })
    if not stages:
        stages.append({
            "stage_name": "智能体任务执行",
            "stage_evidence_span_ids": [evidence.any_span_id],
        })

    handoffs = []
    if evidence.tool_span_ids and evidence.chat_span_ids:
        handoffs.append({
            "source_span_id": evidence.tool_span_ids[0],
            "used_by_span_id": evidence.chat_span_ids[-1],
            "description": "领域工具返回的预测、影响或路线数据被后续模型汇总用于形成最终研判。",
        })
    elif len(evidence.chat_span_ids) >= 2:
        handoffs.append({
            "source_span_id": evidence.chat_span_ids[0],
            "used_by_span_id": evidence.chat_span_ids[-1],
            "description": "首轮模型规划结果被后续模型回合用于形成最终答复。",
        })
    else:
        handoffs.append({
            "source_span_id": evidence.any_span_id,
            "used_by_span_id": evidence.root_span_id or evidence.any_span_id,
            "description": "同一 trace 内记录了任务输入、执行状态和输出结果。",
        })

    return {
        "submission_task_id": "E1-flood-run-001",
        "trace_id": evidence.trace_id,
        "task_description": evidence.task_description,
        "declared_stages": stages,
        "deliverable_reference": "traces.json",
        "deliverable_hash": traces_hash,
        "process_handoff_evidence": handoffs,
    }


def build_tool_skill_examples() -> list[dict[str, Any]]:
    return [
        {
            "tool_or_skill_name": "run_flood_forecast",
            "aliases": ["CNN 水动力预测", "洪水预测"],
            "purpose": "基于当前边界流量和珊瑚河流域模型运行 CNN 水动力预测，生成可用于淹没展示和影响分析的预测结果。",
            "parameter_mode": "structured",
            "input_parameters": [
                {"name": "forecast_id", "type": "string", "required": False},
                {"name": "force", "type": "boolean", "required": False},
            ],
            "success_return_type": "object",
            "failure_return_form": {
                "type": "object",
                "description": "失败时返回 error/status 字段，说明预测无法生成或输入数据不可用。",
            },
        },
        {
            "tool_or_skill_name": "analyze_inundation_impacts",
            "aliases": ["淹没影响分析", "受影响对象识别"],
            "purpose": "将预测淹没结果与道路、桥梁、居民点等领域对象叠加，识别受影响对象、风险等级和关键原因。",
            "parameter_mode": "structured",
            "input_parameters": [
                {"name": "forecast_id", "type": "string", "required": False},
                {"name": "target_type", "type": "string", "required": False},
                {"name": "min_depth_m", "type": "number", "required": False},
                {"name": "time_h", "type": "number", "required": False},
            ],
            "success_return_type": "object",
            "failure_return_form": {
                "type": "object",
                "description": "失败时返回 error 字段，说明预测结果或对象库不可用。",
            },
        },
        {
            "tool_or_skill_name": "plan_evacuation_route",
            "aliases": ["避洪路线规划", "转移路线规划"],
            "purpose": "根据起点、安置点、预测淹没水深和道路约束规划避洪转移路线。",
            "parameter_mode": "structured",
            "input_parameters": [
                {"name": "start_object_type", "type": "string", "required": False},
                {"name": "start_object_id", "type": "string", "required": False},
                {"name": "destination_site_id", "type": "string", "required": False},
                {"name": "forecast_id", "type": "string", "required": False},
                {"name": "avoid_flood", "type": "boolean", "required": False},
            ],
            "success_return_type": "object",
            "failure_return_form": {
                "type": "object",
                "description": "失败时返回 error 字段，说明起终点、路网或预测约束不可满足。",
            },
        },
    ]


class EvidenceTrace:
    def __init__(self, trace_id: str, spans: list["SpanView"]):
        self.trace_id = trace_id
        self.spans = sorted(spans, key=lambda item: item.start_time)
        self.root_span_id = next((span.span_id for span in self.spans if span.operation == "invoke_agent"), "")
        self.chat_span_ids = [span.span_id for span in self.spans if span.operation == "chat"]
        self.tool_span_ids = [span.span_id for span in self.spans if span.operation == "execute_tool"]
        self.any_span_id = self.spans[0].span_id if self.spans else ""
        self.task_description = self._task_description()

    def _task_description(self) -> str:
        root = next((span for span in self.spans if span.operation == "invoke_agent"), None)
        message = first_user_message(root.attributes.get("gen_ai.input.messages")) if root else ""
        if not message:
            message = "执行珊瑚河洪水应急智能体任务，完成预测、影响分析、路线规划或应急处置研判。"
        return shorten(" ".join(message.split()), 240)


class SpanView:
    def __init__(self, span: dict[str, Any]):
        self.raw = span
        self.trace_id = str(span.get("traceId") or span.get("trace_id") or "")
        self.span_id = str(span.get("spanId") or span.get("span_id") or "")
        self.start_time = int(str(span.get("startTimeUnixNano") or 0))
        self.attributes = attributes_to_dict(span.get("attributes"))
        self.operation = str(self.attributes.get("gen_ai.operation.name") or "")


def choose_evidence_trace(document: dict[str, Any]) -> EvidenceTrace:
    by_trace: dict[str, list[SpanView]] = {}
    for span in iter_spans(document):
        by_trace.setdefault(span.trace_id, []).append(span)
    if not by_trace:
        raise SubmissionBuildError("traces.json contains no spans")
    candidates = [EvidenceTrace(trace_id, spans) for trace_id, spans in by_trace.items()]
    candidates = [item for item in candidates if item.root_span_id] or candidates
    return max(
        candidates,
        key=lambda item: (
            len(item.tool_span_ids),
            len(item.chat_span_ids),
            len(item.spans),
            item.spans[0].start_time if item.spans else 0,
        ),
    )


def iter_spans(document: dict[str, Any]) -> list[SpanView]:
    spans: list[SpanView] = []
    for resource_span in document.get("resourceSpans", []):
        if not isinstance(resource_span, dict):
            continue
        for scope_span in resource_span.get("scopeSpans", []):
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans", []):
                if isinstance(span, dict):
                    spans.append(SpanView(span))
    return spans


def attributes_to_dict(attributes: Any) -> dict[str, Any]:
    if not isinstance(attributes, list):
        return {}
    result: dict[str, Any] = {}
    for attribute in attributes:
        if not isinstance(attribute, dict) or not isinstance(attribute.get("key"), str):
            continue
        result[attribute["key"]] = otlp_any_value(attribute.get("value"))
    return result


def otlp_any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if key in value:
            raw = value[key]
            if key == "stringValue" and isinstance(raw, str):
                stripped = raw.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        return json.loads(stripped)
                    except json.JSONDecodeError:
                        return raw
            return raw
    if "arrayValue" in value:
        values = value["arrayValue"].get("values", []) if isinstance(value["arrayValue"], dict) else []
        return [otlp_any_value(item) for item in values]
    if "kvlistValue" in value:
        values = value["kvlistValue"].get("values", []) if isinstance(value["kvlistValue"], dict) else []
        return {item.get("key", ""): otlp_any_value(item.get("value")) for item in values if isinstance(item, dict)}
    return value


def first_user_message(value: Any) -> str:
    messages = value.get("messages") if isinstance(value, dict) else value
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        pieces: list[str] = []
        content = message.get("content")
        if isinstance(content, str):
            pieces.append(content)
        parts = message.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("content"), str):
                    pieces.append(part["content"])
        if pieces:
            return "\n".join(pieces)
    return ""


def shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


if __name__ == "__main__":
    raise SystemExit(main())
