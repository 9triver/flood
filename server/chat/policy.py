from __future__ import annotations

import json
import re

from oag.ontology.schema import Ontology


def _question_text(message: str) -> str:
    return re.sub(r"\s+", "", str(message or "")).strip("。？！?!")


def is_flood_status_question(message: str) -> bool:
    """Only unambiguously global status questions restrict the available tools."""
    text = _question_text(message)
    return bool(re.fullmatch(
        r"(?:请问|查询一下|查询|查看一下|查看)?(?:现在|当前|目前|未来24小时)?"
        r"(?:是否有|有没有|有无|有)(?:预测)?(?:淹没区|淹水区|积水区|演水区|淹没|淹水|积水)(?:吗|么|呢)?",
        text,
    ))


def build_agent_task_hint(message: str, ontology: Ontology) -> str:
    text = _question_text(message)
    if (any(word in text for word in ("降雨", "降水", "雨量"))
            and any(word in text for word in ("假设", "如果", "假如", "倍", "增加", "减少", "调整", "试算"))):
        return (
            "这是降水假设场景请求，调用 simulate_flood_scenario。rainfall_multiplier 按用户假设设置，"
            "降水倍数不明确时先澄清；from_time_h/to_time_h 为相对 t0 的整点小时，调整左开右闭时段。"
            "默认只调整原预测 t0 之后24小时的未来降水，沿用原调度模式，不能修改原始 CSV、覆盖正式预测或声称方案已应用。"
            "time_h 是用户关心的 t1，默认本轮地图选中帧；按返回的 reservoir_safety、selected_time 和 window_envelope"
            "解释水库安全与道路影响，失败或 partial 不能解释为无淹没。"
        )
    if any(word in text for word in ("调度", "下泄", "泄洪", "试算")):
        return (
            "调度调整请求使用 get_longtan_dispatch_plan 查询本轮预测 t0 的水库状态与方案，"
            "再用 simulate_longtan_dispatch 试算。time_h 是用户关心的 t1，默认本轮地图选中帧；"
            "调度起点始终为原预测 t0。模式目标不明确时询问用户，不能编造目标值。"
            "根据工具返回先说明水库24小时安全校核，再比较 t1 和整个24小时道路影响。"
            "该流程只试算和分析，不能声称已切换正式方案或用 run_flood_forecast 覆盖原预测。"
        )
    if is_flood_status_question(text):
        return ("本轮只查询淹没状态，调用 get_flood_status；问现在时使用本轮分析时刻（view=current），"
                "明确问未来总体时使用 view=envelope。演进未开始时按工具返回说明当前为无洪水初始状态；演进开始后预测不可用时说明无法判断，"
                "只有要查看未来预测时才提示先开始演进；回复不输出内部ID或状态字段。"
                "不得运行预测、自动展示地图、开启警戒或把未来最大包络当作当前淹水。")
    policy = ontology.interaction_policies.get("user_chat")
    count_intent = policy.intents.get("count") if policy else None
    if not count_intent:
        return ""
    prefix = r"(?:请问|请统计|统计|查询)?(?:珊瑚河流域(?:内)?|全流域|全部|一共|总共)?"
    quantity = r"(?:几个|多少个|几条|多少条|多少座|几座|多少处|多少)"
    for object_type, definition in ontology.objects.items():
        if not definition.countable:
            continue
        for alias in definition.aliases:
            for term in alias.terms:
                escaped = re.escape(term)
                patterns = (
                    prefix + rf"(?:有)?{quantity}{escaped}",
                    prefix + rf"{escaped}(?:一共|总共)?(?:有)?{quantity}",
                    prefix + rf"{escaped}(?:的)?(?:数量|总数)(?:是多少)?",
                )
                if not any(re.fullmatch(pattern, text) for pattern in patterns):
                    continue
                args = {"object_type": object_type}
                if alias.filters:
                    args["filters"] = alias.filters
                return count_intent.task_hint.format(calls=f"count({json.dumps(args, ensure_ascii=False)})")
    # A qualified count is deliberately left to normal tool composition.
    if any(word in text for word in count_intent.keywords):
        return ("数量问题须保留用户的地域、距离、类别、受淹和时间条件。先确定完整对象范围再统计；"
                "附近查询使用 total_matched，不能把当前页条数当作总数；受淹数量使用影响分析的范围与汇总，"
                "不能用全库 count 代替。用户没有给出可解析范围时先澄清。")
    return ""
