import json
import re
from datetime import date
from typing import Any

from app.config import settings
from app.llm import LLMError, chat_completion, llm_configured
from app.state import get_active_goal, get_daily_plan, upsert_active_goal, upsert_daily_plan


DAILY_PLAN_PROMPT = """你是健康计划助手。请基于用户目标，输出“今天”的健康执行计划。
只输出 JSON，不要 markdown，不要解释。格式：
{
  "goal_text": "字符串",
  "water_target_ml": 整数,
  "water_start": "HH:MM",
  "water_end": "HH:MM",
  "breakfast_time": "HH:MM",
  "lunch_time": "HH:MM",
  "dinner_time": "HH:MM",
  "sleep_time": "HH:MM",
  "exercise_minutes": 整数,
  "exercise_focus": "一句话",
  "notes": "一句话"
}
约束：
- water_target_ml 范围 1200-3500。
- exercise_minutes 范围 10-90。
- 时间全部使用 24 小时制 HH:MM。
- 计划要温和、可持续，不要激进。
"""


def ensure_today_plan() -> dict[str, str | int]:
    today = date.today().isoformat()
    existing = get_daily_plan(today)
    if existing:
        return existing

    goal = get_active_goal()
    if not goal:
        upsert_active_goal(settings.health_primary_goal, settings.health_goal_constraints)
        goal = {"goal_text": settings.health_primary_goal, "constraints_text": settings.health_goal_constraints}

    plan = _default_plan(goal["goal_text"])
    upsert_daily_plan(plan, today)
    created = get_daily_plan(today)
    return created or plan


async def ensure_today_plan_async() -> dict[str, str | int]:
    today = date.today().isoformat()
    existing = get_daily_plan(today)
    if existing:
        return existing

    goal = get_active_goal()
    if not goal:
        upsert_active_goal(settings.health_primary_goal, settings.health_goal_constraints)
        goal = {"goal_text": settings.health_primary_goal, "constraints_text": settings.health_goal_constraints}

    plan = _default_plan(goal["goal_text"])
    if llm_configured():
        try:
            llm_plan = await _llm_plan_async(goal["goal_text"], goal.get("constraints_text", ""))
            plan.update(llm_plan)
            plan["source"] = "llm"
        except LLMError:
            pass

    upsert_daily_plan(plan, today)
    created = get_daily_plan(today)
    return created or plan


async def adapt_today_plan_async(feedback_text: str) -> dict[str, str | int]:
    current = await ensure_today_plan_async()
    updated = dict(current)
    note_parts: list[str] = []
    text = feedback_text.strip()
    feedback_type = classify_feedback_type(text)

    exercise_match = re.search(r"(?:运动|锻炼)\s*(\d{1,3})", text)
    if exercise_match:
        minutes = _bounded_int(exercise_match.group(1), 0, 120, int(updated.get("exercise_minutes") or 30))
        updated["exercise_minutes"] = minutes
        note_parts.append(f"运动调整为 {minutes} 分钟")

    if any(token in text for token in ("不运动", "取消运动", "今天休息", "先不锻炼")):
        updated["exercise_minutes"] = 0
        updated["exercise_focus"] = "今天以恢复休息为主"
        note_parts.append("今天运动改为休息")

    water_match = re.search(r"(?:喝水|饮水).{0,8}?(\d{3,4})", text)
    if water_match:
        target = _bounded_int(water_match.group(1), 1200, 3500, int(updated.get("water_target_ml") or 2000))
        updated["water_target_ml"] = target
        note_parts.append(f"饮水目标调整为 {target} ml")

    sleep_match = re.search(r"([01]\d|2[0-3]):([0-5]\d)", text)
    if sleep_match and any(token in text for token in ("睡", "入睡", "休息")):
        sleep_time = f"{sleep_match.group(1)}:{sleep_match.group(2)}"
        updated["sleep_time"] = sleep_time
        note_parts.append(f"入睡目标调整为 {sleep_time}")

    if any(token in text for token in ("加班", "太忙", "晚点", "推迟")) and not sleep_match:
        updated["sleep_time"] = "23:30"
        note_parts.append("检测到日程偏忙，今晚入睡目标临时放宽到 23:30")

    if feedback_type == "postpone":
        updated["exercise_minutes"] = max(10, int(int(updated.get("exercise_minutes") or 30) * 0.8))
        note_parts.append("按延期反馈，今日运动负荷下调约 20%")
    elif feedback_type == "discomfort":
        updated["exercise_minutes"] = 0
        updated["exercise_focus"] = "今天以休息与恢复为主，如不适持续请及时就医"
        note_parts.append("按身体不适反馈，今日运动暂停")
    elif feedback_type == "skip":
        updated["exercise_minutes"] = 0
        note_parts.append("按跳过反馈，今日计划降载")

    if not note_parts and llm_configured():
        try:
            llm_updated = await _llm_replan_async(current, text)
            updated.update(llm_updated)
            note_parts.append("已根据你的反馈重排今天计划")
        except LLMError:
            pass

    if not note_parts:
        note_parts.append("收到你的反馈，先保持今天计划不变")

    updated["notes"] = f"feedback_type={feedback_type}；" + "；".join(note_parts)
    updated["source"] = f"feedback_{feedback_type}"
    upsert_daily_plan(updated, date.today().isoformat())
    final = get_daily_plan()
    return final or updated


def daily_plan_summary(plan: dict[str, str | int]) -> str:
    feedback_type = extract_feedback_type(str(plan.get("notes") or ""))
    return (
        f"今日计划已更新：\n"
        f"- 调整类型：{feedback_type}\n"
        f"- 饮水目标：{plan['water_target_ml']} ml（{plan['water_start']}-{plan['water_end']}）\n"
        f"- 三餐：早 {plan['breakfast_time']} / 午 {plan['lunch_time']} / 晚 {plan['dinner_time']}\n"
        f"- 睡眠：{plan['sleep_time']}\n"
        f"- 运动：{plan['exercise_minutes']} 分钟（{plan['exercise_focus']}）\n"
        f"- 备注：{plan['notes']}"
    )


def classify_feedback_type(text: str) -> str:
    if any(token in text for token in ("不舒服", "头疼", "恶心", "疼", "生病", "难受", "不适")):
        return "discomfort"
    if any(token in text for token in ("不做", "取消", "不运动", "跳过", "算了")):
        return "skip"
    if any(token in text for token in ("推迟", "晚点", "延后", "改天", "加班", "太忙")):
        return "postpone"
    if any(token in text for token in ("改", "调整", "改成", "换成", "设为")):
        return "adjust"
    return "adjust"


def extract_feedback_type(notes: str) -> str:
    match = re.search(r"feedback_type=(postpone|skip|adjust|discomfort)", notes)
    if match:
        return match.group(1)
    return "adjust"


async def _llm_plan_async(goal_text: str, constraints_text: str) -> dict[str, str | int]:
    raw = await _llm_plan_raw_async(goal_text, constraints_text)
    data = _parse_json_object(raw)
    return _normalize_plan(data, goal_text, "llm")


async def _llm_replan_async(current: dict[str, str | int], feedback_text: str) -> dict[str, str | int]:
    prompt = """你是健康计划重规划助手。根据用户反馈，修改“仅今天”的计划。
只输出 JSON，不要解释，字段与输入计划一致。
要求：计划必须可执行、温和、可持续。"""
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"当前计划：{json.dumps(current, ensure_ascii=False)}\n"
                f"用户反馈：{feedback_text}\n"
            ),
        },
    ]
    raw = await chat_completion(messages)
    data = _parse_json_object(raw)
    return _normalize_plan(data, str(current.get("goal_text") or settings.health_primary_goal), "feedback")


async def _llm_plan_raw_async(goal_text: str, constraints_text: str) -> str:
    messages = [
        {"role": "system", "content": DAILY_PLAN_PROMPT},
        {
            "role": "user",
            "content": (
                f"今天日期：{date.today().isoformat()}\n"
                f"用户目标：{goal_text}\n"
                f"约束：{constraints_text or '无'}\n"
                f"默认作息：起床 {settings.wake_time}，睡觉 {settings.sleep_time}\n"
                f"默认饮水目标：{settings.water_daily_goal_ml} ml\n"
            ),
        },
    ]
    return await chat_completion(messages)


def _default_plan(goal_text: str) -> dict[str, str | int]:
    return {
        "goal_text": goal_text,
        "water_target_ml": settings.water_daily_goal_ml,
        "water_start": settings.water_remind_start,
        "water_end": settings.water_remind_end,
        "breakfast_time": settings.meal_breakfast_time,
        "lunch_time": settings.meal_lunch_time,
        "dinner_time": settings.meal_dinner_time,
        "sleep_time": settings.sleep_time,
        "exercise_minutes": 30,
        "exercise_focus": "中等强度、可持续完成即可",
        "notes": "按计划执行，若有加班或不适再调整。",
        "source": "rule",
    }


def _normalize_plan(data: dict[str, Any], goal_text: str, source: str) -> dict[str, str | int]:
    default = _default_plan(goal_text)
    water_target = _bounded_int(data.get("water_target_ml"), 1200, 3500, int(default["water_target_ml"]))
    exercise_minutes = _bounded_int(data.get("exercise_minutes"), 10, 90, int(default["exercise_minutes"]))
    return {
        "goal_text": str(data.get("goal_text") or goal_text),
        "water_target_ml": water_target,
        "water_start": _norm_time(str(data.get("water_start") or default["water_start"]), str(default["water_start"])),
        "water_end": _norm_time(str(data.get("water_end") or default["water_end"]), str(default["water_end"])),
        "breakfast_time": _norm_time(str(data.get("breakfast_time") or default["breakfast_time"]), str(default["breakfast_time"])),
        "lunch_time": _norm_time(str(data.get("lunch_time") or default["lunch_time"]), str(default["lunch_time"])),
        "dinner_time": _norm_time(str(data.get("dinner_time") or default["dinner_time"]), str(default["dinner_time"])),
        "sleep_time": _norm_time(str(data.get("sleep_time") or default["sleep_time"]), str(default["sleep_time"])),
        "exercise_minutes": exercise_minutes,
        "exercise_focus": str(data.get("exercise_focus") or default["exercise_focus"]),
        "notes": str(data.get("notes") or default["notes"]),
        "source": source,
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise LLMError("Daily planner LLM did not return JSON")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise LLMError("Daily planner JSON is not an object")
    return data


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, result))


def _norm_time(value: str, fallback: str) -> str:
    match = re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", value.strip())
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return fallback
