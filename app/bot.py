import json
import logging
import re
from datetime import date, datetime, time
from typing import Any, TypedDict

from app.config import settings
from app.llm import LLMError, SYSTEM_PROMPT, chat_completion, llm_configured
from app.planner.goal_planner import adapt_today_plan_async, daily_plan_summary, ensure_today_plan_async
from app.planner.meal_analysis import analyze_meal
from app.planner.meal_planner import get_menu_async, menu_for_day
from app.state import (
    health_context_snapshot,
    meal_events_between,
    menstrual_prediction,
    recent_messages,
    record_event,
    record_menstrual_end,
    record_menstrual_start,
    remember_meal_analysis,
    remember_message,
    today_summary,
    today_water_progress,
)


logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False


FALLBACK_HELP = (
    "我可以记录和提醒喝水、吃饭、睡眠、运动。\n"
    "可以这样回复：喝了 300、今日菜单、吃了午餐、睡了 7.5、运动 30、今日状态。"
)


INTENT_PROMPT = """你是健康助手的意图理解器。
请把用户消息解析为严格 JSON，不要输出 Markdown 或解释。
JSON 格式：
{
  "intent": "log|query|advice|menu|chat|clarify|feedback",
  "actions": [
    {
      "type": "record_water|record_meal|record_sleep|record_exercise|record_period_start|record_period_end|query_period|query_today_summary|query_water_remaining|query_meal|get_menu|query_today_plan|plan_feedback|none",
      "amount": 数字或 null,
      "unit": "ml|hours|minutes|count|none",
      "note": "简短中文备注",
      "feedback_type": "postpone|skip|adjust|discomfort|none"
    }
  ],
  "needs_reply_generation": true,
  "clarifying_question": "需要追问时填写，否则为空字符串"
}
规则：
- 用户表达喝水但没有数量时，按 250 ml 记录，除非用户明显不是在记录。
- 用户表达运动但没有分钟数时，按 30 minutes 记录。
- 用户表达睡眠但没有时长时，不记录，改为 clarify。
- 用户表达吃了某餐或某种食物时，记录 record_meal，amount 为 null。
- 用户表达生理期/月经来了、开始了，记录 record_period_start。
- 用户表达生理期/月经结束了，记录 record_period_end。
- 用户询问下次生理期、经期预测、周期情况，使用 query_period。
- 用户问状态/总结/今天怎么样时，使用 query_today_summary。
- 用户问“今天还要喝多少水/还差多少水”时，使用 query_water_remaining。
- 用户询问“我早上/中午/晚上吃了什么”这类已记录餐次回查时，使用 query_meal。
- 用户问菜单/吃什么/菜谱时，使用 get_menu；若提到具体餐次（早餐/午餐/晚餐），note 里保留餐次。
- 用户询问“今天计划”“今日安排”时，使用 query_today_plan。
- 用户表达要调整今天计划时（如“今天不运动”“运动改成20分钟”“今晚晚点睡”），使用 plan_feedback。
- plan_feedback 时请同时给 feedback_type：
  postpone=推迟/加班晚点，skip=取消/跳过，adjust=普通调整，discomfort=身体不适。
- 健康咨询但不需要记录时，actions 使用 none。
- 只能使用上面列出的 action type。
"""


REPLY_PROMPT = f"""{SYSTEM_PROMPT}
你正在根据系统已经执行过的健康工具结果回复用户。
要求：
- 先确认已记录或已查询到的事实。
- 给出 1-3 条具体建议。
- 不要虚构系统没有提供的数据。
- 如果涉及明显疾病、急性疼痛、严重不适或用药，建议就医。
"""


class AgentState(TypedDict, total=False):
    conversation_id: str
    text: str
    plan: dict[str, Any]
    results: list[dict[str, Any]]
    replan: dict[str, str | int]
    reply: str


async def _rule_reply(text: str) -> str | None:
    normalized = text.strip().replace("，", ",")

    water = re.search(r"(?:喝了|喝水)\s*(\d+)?", normalized)
    if water:
        amount = int(water.group(1) or 250)
        record_event("water", amount, normalized)
        return f"已记录喝水 {amount} ml。\n\n{today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal)}"

    if "还要喝多少水" in normalized or "还差多少水" in normalized:
        return _water_remaining_reply()

    meal_name = _meal_name_from_text(normalized)
    if "菜单" in normalized or "菜谱" in normalized or "吃什么" in normalized:
        if meal_name:
            return await get_menu_async(meal_name=meal_name, strategy="adaptive")
        return menu_for_day()

    if _is_period_start(normalized):
        record_menstrual_start(note=normalized)
        return _period_reply("已记录本次生理期开始。")

    if _is_period_end(normalized):
        recorded = record_menstrual_end(note=normalized)
        if not recorded:
            return "没有找到未结束的生理期记录。可以回复“生理期来了”先记录开始日期。"
        return _period_reply("已记录本次生理期结束。")

    if _is_period_query(normalized):
        return _period_reply("生理期预测")

    if "吃了" in normalized:
        event_id = record_event("meal", None, normalized)
        analysis = await analyze_meal(normalized)
        if analysis:
            remember_meal_analysis(event_id, analysis)
        return _meal_record_reply(analysis)

    sleep = re.search(r"睡了\s*(\d+(?:\.\d+)?)", normalized)
    if sleep:
        hours = float(sleep.group(1))
        record_event("sleep", hours, normalized)
        return f"已记录睡眠 {hours:g} 小时。"

    exercise = re.search(r"(?:运动|锻炼)\s*(\d+)?", normalized)
    if exercise:
        minutes = int(exercise.group(1) or 30)
        record_event("exercise", minutes, normalized)
        return f"已记录运动 {minutes} 分钟。\n\n{today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal)}"

    if "状态" in normalized or "总结" in normalized:
        return today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal)
    if _looks_like_meal_query(normalized):
        return _query_meal_reply(normalized)

    return None


async def _plan_actions(conversation_id: str, text: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": INTENT_PROMPT},
        {
            "role": "system",
            "content": (
                f"用户名称：{settings.user_name}\n"
                f"作息：{settings.wake_time} 起床，{settings.sleep_time} 睡觉\n"
                f"{health_context_snapshot()}\n"
            ),
        },
    ]
    messages.extend(recent_messages(conversation_id, settings.llm_context_limit))
    messages.append({"role": "user", "content": text})
    raw = await chat_completion(messages)
    return _parse_json_object(raw)


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise LLMError("LLM did not return JSON")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("LLM returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise LLMError("LLM JSON is not an object")
    return data


async def _execute_actions(plan: dict[str, Any], original_text: str) -> list[dict[str, Any]]:
    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        actions = []

    results: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue

        action_type = str(action.get("type", "none"))
        note = str(action.get("note") or original_text)
        amount = _number_or_none(action.get("amount"))

        if action_type == "record_water":
            ml = int(amount or 250)
            record_event("water", ml, note)
            results.append({"type": action_type, "status": "recorded", "amount": ml, "unit": "ml", "note": note})
        elif action_type == "record_meal":
            meal_note = original_text.strip() or note
            event_id = record_event("meal", None, meal_note)
            results.append({"type": action_type, "status": "recorded", "note": meal_note, "event_id": event_id})
        elif action_type == "record_sleep":
            if amount is None:
                results.append({"type": action_type, "status": "needs_clarification", "message": "缺少睡眠时长"})
            else:
                record_event("sleep", amount, note)
                results.append({"type": action_type, "status": "recorded", "amount": amount, "unit": "hours", "note": note})
        elif action_type == "record_exercise":
            minutes = int(amount or 30)
            record_event("exercise", minutes, note)
            results.append({"type": action_type, "status": "recorded", "amount": minutes, "unit": "minutes", "note": note})
        elif action_type == "record_period_start":
            record_menstrual_start(note=note)
            results.append({"type": action_type, "status": "recorded", "note": note})
        elif action_type == "record_period_end":
            recorded = record_menstrual_end(note=note)
            results.append({"type": action_type, "status": "recorded" if recorded else "not_found", "note": note})
        elif action_type == "query_period":
            results.append({"type": action_type, "status": "ok", "content": _period_reply("生理期预测")})
        elif action_type == "query_today_summary":
            results.append({"type": action_type, "status": "ok", "content": today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal)})
        elif action_type == "query_water_remaining":
            results.append({"type": action_type, "status": "ok", "content": _water_remaining_reply()})
        elif action_type == "query_meal":
            query_note = original_text.strip() or note
            results.append({"type": action_type, "status": "ok", "content": _query_meal_reply(query_note)})
        elif action_type == "get_menu":
            meal_name = _meal_name_from_text(original_text) or _meal_name_from_text(note)
            strategy = "adaptive" if meal_name else "template"
            content = await get_menu_async(meal_name=meal_name, strategy=strategy)
            results.append({"type": action_type, "status": "ok", "content": content})
        elif action_type == "query_today_plan":
            today_plan = await ensure_today_plan_async()
            results.append({"type": action_type, "status": "ok", "content": daily_plan_summary(today_plan)})
        elif action_type == "plan_feedback":
            results.append({"type": action_type, "status": "received", "note": original_text.strip() or note})
        elif action_type == "none":
            results.append({"type": action_type, "status": "skipped"})
        else:
            results.append({"type": action_type, "status": "ignored", "message": "unsupported action"})

    if not results:
        results.append({"type": "none", "status": "skipped"})
    return results


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


async def _generate_reply(conversation_id: str, text: str, plan: dict[str, Any], results: list[dict[str, Any]]) -> str:
    clarifying_question = str(plan.get("clarifying_question") or "").strip()
    if plan.get("intent") == "clarify" and clarifying_question:
        return clarifying_question

    messages = [
        {"role": "system", "content": REPLY_PROMPT},
        {
            "role": "system",
            "content": (
                f"用户名称：{settings.user_name}\n"
                f"健康上下文：\n{health_context_snapshot()}\n"
                f"意图解析：{json.dumps(plan, ensure_ascii=False)}\n"
                f"工具执行结果：{json.dumps(results, ensure_ascii=False)}"
            ),
        },
    ]
    messages.extend(recent_messages(conversation_id, min(settings.llm_context_limit, 6)))
    messages.append({"role": "user", "content": text})
    return await chat_completion(messages)


async def _smart_reply(conversation_id: str, text: str) -> str:
    if LANGGRAPH_AVAILABLE:
        if _SMART_AGENT_GRAPH is None:
            raise LLMError("LangGraph is not initialized")
        state = await _SMART_AGENT_GRAPH.ainvoke({"conversation_id": conversation_id, "text": text})
        reply = state.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply
        raise LLMError("LangGraph returned empty reply")

    return await _smart_reply_legacy(conversation_id, text)


async def _smart_reply_legacy(conversation_id: str, text: str) -> str:
    plan = await _plan_actions(conversation_id, text)
    results = await _execute_actions(plan, text)
    if _needs_replan(results):
        replan = await _run_replan(results)
        if replan:
            return daily_plan_summary(replan)
    await _enrich_meal_results(results)
    if _requires_deterministic_reply(results):
        return _tool_result_reply(results)

    try:
        return await _generate_reply(conversation_id, text, plan, results)
    except LLMError as exc:
        logger.warning("LLM final reply failed after tool execution: %s", exc)
        return _tool_result_reply(results)


async def _plan_node(state: AgentState) -> AgentState:
    conversation_id = str(state.get("conversation_id", "default"))
    text = str(state.get("text", ""))
    plan = await _plan_actions(conversation_id, text)
    return {"plan": plan}


async def _act_node(state: AgentState) -> AgentState:
    text = str(state.get("text", ""))
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    results = await _execute_actions(plan, text)
    await _enrich_meal_results(results)
    return {"results": results}


async def _replan_node(state: AgentState) -> AgentState:
    results = state.get("results")
    if not isinstance(results, list):
        return {"reply": FALLBACK_HELP}
    replan = await _run_replan(results)
    if not replan:
        return {"reply": "收到你的反馈，今天计划暂不调整。"}
    return {"replan": replan, "reply": daily_plan_summary(replan)}


def _route_after_act(state: AgentState) -> str:
    results = state.get("results")
    if isinstance(results, list) and _needs_replan(results):
        return "replan"
    if isinstance(results, list) and _requires_deterministic_reply(results):
        return "deterministic_reply"
    return "llm_reply"


def _deterministic_reply_node(state: AgentState) -> AgentState:
    results = state.get("results")
    if not isinstance(results, list):
        return {"reply": FALLBACK_HELP}
    return {"reply": _tool_result_reply(results)}


async def _llm_reply_node(state: AgentState) -> AgentState:
    conversation_id = str(state.get("conversation_id", "default"))
    text = str(state.get("text", ""))
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    results = state.get("results") if isinstance(state.get("results"), list) else []
    try:
        reply = await _generate_reply(conversation_id, text, plan, results)
    except LLMError as exc:
        logger.warning("LLM final reply failed after tool execution: %s", exc)
        reply = _tool_result_reply(results)
    return {"reply": reply}


def _build_smart_agent_graph() -> Any | None:
    if not LANGGRAPH_AVAILABLE:
        logger.warning("LangGraph is not installed; falling back to legacy smart pipeline")
        return None

    graph = StateGraph(AgentState)
    graph.add_node("plan_step", _plan_node)
    graph.add_node("act_step", _act_node)
    graph.add_node("replan_step", _replan_node)
    graph.add_node("deterministic_reply_step", _deterministic_reply_node)
    graph.add_node("llm_reply_step", _llm_reply_node)
    graph.add_edge(START, "plan_step")
    graph.add_edge("plan_step", "act_step")
    graph.add_conditional_edges(
        "act_step",
        _route_after_act,
        {
            "replan": "replan_step",
            "deterministic_reply": "deterministic_reply_step",
            "llm_reply": "llm_reply_step",
        },
    )
    graph.add_edge("replan_step", END)
    graph.add_edge("deterministic_reply_step", END)
    graph.add_edge("llm_reply_step", END)
    return graph.compile()


_SMART_AGENT_GRAPH = _build_smart_agent_graph()


def _requires_deterministic_reply(results: list[dict[str, Any]]) -> bool:
    deterministic_types = {
        "record_water",
        "record_meal",
        "record_sleep",
        "record_exercise",
        "record_period_start",
        "record_period_end",
        "query_period",
        "query_today_summary",
        "query_water_remaining",
        "query_meal",
        "get_menu",
        "query_today_plan",
    }
    return any(result.get("type") in deterministic_types for result in results)


def _needs_replan(results: list[dict[str, Any]]) -> bool:
    return any(result.get("type") == "plan_feedback" for result in results)


async def _run_replan(results: list[dict[str, Any]]) -> dict[str, str | int] | None:
    feedbacks = [str(result.get("note") or "").strip() for result in results if result.get("type") == "plan_feedback"]
    feedback_text = "；".join([item for item in feedbacks if item])
    if not feedback_text:
        return None
    return await adapt_today_plan_async(feedback_text)


def _tool_result_reply(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for result in results:
        result_type = result.get("type")
        status = result.get("status")
        if result_type == "record_water" and status == "recorded":
            lines.append(f"已记录喝水 {result['amount']} ml。")
        elif result_type == "record_meal" and status == "recorded":
            lines.append(_meal_record_reply(result.get("analysis") if isinstance(result.get("analysis"), dict) else None))
        elif result_type == "record_sleep" and status == "recorded":
            lines.append(f"已记录睡眠 {result['amount']:g} 小时。")
        elif result_type == "record_sleep" and status == "needs_clarification":
            lines.append("你睡了多久？可以直接告诉我，比如：睡了 7.5。")
        elif result_type == "record_exercise" and status == "recorded":
            lines.append(f"已记录运动 {result['amount']} 分钟。")
        elif result_type == "record_period_start" and status == "recorded":
            lines.append(_period_reply("已记录本次生理期开始。"))
        elif result_type == "record_period_end" and status == "recorded":
            lines.append(_period_reply("已记录本次生理期结束。"))
        elif result_type == "record_period_end" and status == "not_found":
            lines.append("没有找到未结束的生理期记录。可以回复“生理期来了”先记录开始日期。")
        elif result_type == "query_period" and result.get("content"):
            lines.append(str(result["content"]))
        elif result_type in {"query_today_summary", "query_water_remaining", "query_meal", "get_menu", "query_today_plan"} and result.get("content"):
            lines.append(str(result["content"]))

    if not lines:
        return FALLBACK_HELP

    if not any(result.get("type") in {"query_today_summary", "query_water_remaining", "query_meal", "get_menu", "query_period"} for result in results):
        lines.append(today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal))
    return "\n\n".join(lines)


def _meal_name_from_text(text: str) -> str | None:
    if any(token in text for token in ("早餐", "早饭", "早上", "上午")):
        return "早餐"
    if any(token in text for token in ("午餐", "午饭", "中午")):
        return "午餐"
    if any(token in text for token in ("晚餐", "晚饭", "晚上", "今晚")):
        return "晚餐"
    return None


def _water_remaining_reply() -> str:
    progress = today_water_progress(settings.water_daily_goal_ml)
    return (
        "今日饮水进度\n"
        f"- 已喝：{progress['consumed_ml']} ml\n"
        f"- 目标：{progress['target_ml']} ml\n"
        f"- 还需：{progress['remaining_ml']} ml"
    )


def _looks_like_meal_query(text: str) -> bool:
    if "吃了什么" not in text and "吃的什么" not in text and "吃了啥" not in text and "吃的啥" not in text:
        return False
    return any(token in text for token in ("早", "早餐", "上午", "中午", "午餐", "晚上", "晚餐", "今天"))


def _query_meal_reply(text: str) -> str:
    meal_name, start_at, end_at = _meal_window_for_query(text)
    day = date.today()
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day, time(23, 59, 59))
    window_start = datetime.combine(day, start_at)
    window_end = datetime.combine(day, end_at)
    day_records = meal_events_between(day_start, day_end)

    if meal_name == "今天":
        records = day_records
    else:
        records = []
        for item in day_records:
            slot = _meal_slot_from_note(str(item.get("note") or ""))
            if slot == meal_name:
                records.append(item)
        if not records:
            # Fallback for old records without explicit meal keywords.
            records = [
                item
                for item in day_records
                if isinstance(item.get("created_at"), str)
                and window_start <= datetime.fromisoformat(str(item["created_at"])) < window_end
            ]

    notes = [str(item.get("note") or "").strip() for item in records if str(item.get("note") or "").strip()]
    if not notes:
        if meal_name == "今天":
            return "今天还没有用餐记录。"
        return f"{meal_name}还没有记录。"

    if meal_name == "今天":
        return "今天记录的饮食有：\n- " + "\n- ".join(notes[-5:])
    return f"{meal_name}记录是：\n- " + "\n- ".join(notes[-3:])


def _meal_window_for_query(text: str) -> tuple[str, time, time]:
    if any(token in text for token in ("早", "早餐", "上午")):
        return "早餐", time(4, 0), time(10, 30)
    if any(token in text for token in ("中午", "午餐")):
        return "午餐", time(10, 30), time(15, 30)
    if any(token in text for token in ("晚上", "晚餐")):
        return "晚餐", time(15, 30), time(23, 59, 59)
    return "今天", time.min, time(23, 59, 59)


def _meal_slot_from_note(note: str) -> str | None:
    if any(token in note for token in ("早餐", "早饭", "早上", "上午")):
        return "早餐"
    if any(token in note for token in ("午餐", "午饭", "中午")):
        return "午餐"
    if any(token in note for token in ("晚餐", "晚饭", "晚上")):
        return "晚餐"
    return None


async def _enrich_meal_results(results: list[dict[str, Any]]) -> None:
    for result in results:
        if result.get("type") != "record_meal" or result.get("status") != "recorded":
            continue
        event_id = result.get("event_id")
        note = str(result.get("note") or "")
        if not isinstance(event_id, int):
            continue
        analysis = await analyze_meal(note)
        if analysis:
            remember_meal_analysis(event_id, analysis)
            result["analysis"] = analysis


def _meal_record_reply(analysis: dict[str, Any] | None) -> str:
    if not analysis:
        return "已记录用餐。"

    lines = ["已记录用餐，并完成饮食分析。"]
    if analysis.get("summary"):
        lines.append(f"本餐概况：{analysis['summary']}")
    if analysis.get("next_meal_focus"):
        lines.append(f"下一餐建议：{analysis['next_meal_focus']}")
    return "\n".join(lines)


def _period_reply(prefix: str) -> str:
    prediction = menstrual_prediction()
    if not prediction.get("has_data"):
        return f"{prefix}\n还没有足够记录预测下次时间。之后可以回复“生理期来了”和“生理期结束了”来建立周期。"

    if prediction.get("current_period"):
        return (
            f"{prefix}\n"
            f"最近一次开始：{prediction['latest_start']}。当前可能处于生理期。\n"
            "饮食建议：保证优质蛋白，搭配温热易消化食物，适量补充含铁食物，少冰冷刺激。"
        )

    return (
        f"{prefix}\n"
        f"最近一次开始：{prediction['latest_start']}，估算周期约 {prediction['cycle_days']} 天。\n"
        f"预计下次开始：{prediction['next_start']}，距离今天约 {prediction['days_until']} 天。\n"
        "临近前 3 天建议规律吃饭，适量增加蛋白质和含铁食物。"
    )


def _is_period_start(text: str) -> bool:
    return ("生理期" in text or "月经" in text or "姨妈" in text) and any(word in text for word in ("来了", "开始", "第一天"))


def _is_period_end(text: str) -> bool:
    return ("生理期" in text or "月经" in text or "姨妈" in text) and any(word in text for word in ("结束", "走了", "没了"))


def _is_period_query(text: str) -> bool:
    return ("生理期" in text or "月经" in text or "姨妈" in text) and any(word in text for word in ("预测", "下次", "什么时候", "周期"))


async def handle_text(text: str, conversation_id: str = "default") -> str:
    await ensure_today_plan_async()
    reply = None
    if llm_configured():
        try:
            reply = await _smart_reply(conversation_id, text)
        except LLMError as exc:
            logger.warning("LLM smart reply failed, falling back to rules: %s", exc)
            reply = await _rule_reply(text)

    if reply is None:
        reply = await _rule_reply(text)

    if reply is None:
        reply = FALLBACK_HELP

    remember_message(conversation_id, "user", text)
    remember_message(conversation_id, "assistant", reply)
    return reply
