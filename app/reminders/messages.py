import asyncio
from datetime import datetime

from app.config import settings
from app.planner.meal_planner import menu_for_meal, menu_for_meal_llm
from app.state import exercise_plan_stats, get_daily_plan, last_event, logged_meal_slots_today, recent_sleep_stats, today_water_ml


def _feedback_type_from_notes(notes: str) -> str:
    for name in ("postpone", "skip", "adjust", "discomfort"):
        if f"feedback_type={name}" in notes:
            return name
    return "adjust"


def water_reminder() -> str | None:
    plan = get_daily_plan()
    water_start = str(plan["water_start"]) if plan else settings.water_remind_start
    water_end = str(plan["water_end"]) if plan else settings.water_remind_end
    water_target_ml = int(plan["water_target_ml"]) if plan else settings.water_daily_goal_ml

    now = datetime.now()
    if not _within_time_window(now, water_start, water_end):
        return None

    current = today_water_ml()
    remaining = max(water_target_ml - current, 0)
    if remaining == 0:
        return None

    recent_water = last_event("water")
    minutes_since_water = None
    if recent_water:
        last_at = datetime.fromisoformat(str(recent_water["created_at"]))
        if last_at.date() == now.date():
            minutes_since_water = int((now - last_at).total_seconds() // 60)
            if minutes_since_water < settings.water_remind_min_interval_minutes:
                return None

    expected = _expected_water_by_now(now, water_target_ml, water_start, water_end)
    if current >= expected - 100:
        return None

    last_part = "今天还没有记录喝水。"
    if minutes_since_water is not None:
        last_part = f"距离上次喝水约 {minutes_since_water} 分钟。"

    suggested = remaining if remaining < 200 else min(350, remaining)
    return (
        f"补水提醒：{last_part}\n"
        f"今天已记录 {current}/{water_target_ml} ml，按当前时间进度应接近 {expected} ml。\n"
        f"建议现在喝 {suggested} ml 左右，回复“喝了 {suggested}”即可记录。"
    )


def meal_reminder(meal_name: str) -> str:
    logged_slots = logged_meal_slots_today()
    previous_slots = _previous_meal_slots(meal_name)
    missing_slots = [slot for slot in previous_slots if slot not in logged_slots]
    if previous_slots and not missing_slots:
        prefix = f"该吃{meal_name}了。你前面餐次已记录，继续保持。回复“吃了{meal_name}”记录完成。"
    elif previous_slots and missing_slots:
        prefix = f"该吃{meal_name}了。你前面还有未记录餐次（{'、'.join(missing_slots)}），如已吃可顺手补记。回复“吃了{meal_name}”记录完成。"
    else:
        prefix = f"该吃{meal_name}了。回复“吃了{meal_name}”记录完成。"
    return f"{prefix}\n\n{menu_for_meal(meal_name)}"


async def meal_reminder_async(meal_name: str) -> str:
    prefix = _meal_reminder_prefix(meal_name)
    menu = menu_for_meal(meal_name)
    try:
        menu = await asyncio.wait_for(menu_for_meal_llm(meal_name), timeout=3.0)
    except (TimeoutError, asyncio.TimeoutError):
        menu = menu_for_meal(meal_name)
    return f"{prefix}\n\n{menu}"


def sleep_reminder() -> str:
    plan = get_daily_plan()
    sleep_target = str(plan["sleep_time"]) if plan else settings.sleep_time
    stats = recent_sleep_stats(7)
    if stats["records"] == 0:
        return (
            f"该准备睡觉了。目标入睡时间 {sleep_target}。\n"
            "这几天睡眠记录还不够，明早可以回复“睡了 7.5”帮我建立你的睡眠规律。"
        )

    avg_hours = float(stats["avg_hours"] or 0)
    short_nights = int(stats["short_nights"] or 0)
    if avg_hours < 7 or short_nights >= 2:
        focus = "最近睡眠偏短，今晚建议提前放下手机，给自己留出 20-30 分钟缓冲。"
    elif avg_hours >= 8:
        focus = "近期睡眠时长不错，今晚重点保持稳定入睡时间。"
    else:
        focus = "近期睡眠基本稳定，今晚继续按目标时间收尾。"

    return (
        f"睡眠提醒：目标入睡时间 {sleep_target}。\n"
        f"近 7 天记录 {stats['records']} 次，平均睡眠约 {stats['avg_hours']} 小时。\n"
        f"{focus}\n"
        "明早回复“睡了 7.5”记录睡眠。"
    )


def exercise_reminder() -> str:
    daily_plan = get_daily_plan()
    today_minutes = int(daily_plan["exercise_minutes"]) if daily_plan else 30
    focus = str(daily_plan["exercise_focus"]) if daily_plan else "中等强度、可持续完成即可"
    feedback_type = _feedback_type_from_notes(str(daily_plan["notes"])) if daily_plan else "adjust"
    stats = exercise_plan_stats()
    if int(stats["remaining"]) == 0:
        return (
            f"本月运动目标已完成：{stats['count']}/{stats['goal']} 次。\n"
            "今天可以做 15-20 分钟拉伸或轻松散步，保持节奏即可。"
        )

    days_since_last = stats["days_since_last"]
    if days_since_last is None:
        timing = "本月还没有运动记录，今天适合先完成一次轻量启动。"
    elif int(days_since_last) >= 3:
        timing = f"距离上次运动已经 {days_since_last} 天，今天建议安排一次。"
    else:
        timing = f"距离上次运动 {days_since_last} 天，可以根据体感选择轻中强度。"

    if int(stats["days_left"]) <= 3 and int(stats["remaining"]) > int(stats["days_left"]):
        pacing_plan = "本月剩余时间已经不适合硬追目标，今天先完成一次轻量运动，月底复盘后下月重新分配节奏。"
    elif float(stats["weekly_needed"]) >= 3:
        pacing_plan = "为了追上目标，接下来建议每周安排 3 次，每次 25-35 分钟。"
    elif float(stats["weekly_needed"]) >= 2:
        pacing_plan = "接下来保持每周 2 次左右，就能比较稳地接近目标。"
    else:
        pacing_plan = "当前进度压力不大，每周 1-2 次维持即可。"

    if feedback_type == "discomfort":
        return (
            "今天检测到你有身体不适反馈，运动提醒降级为恢复建议：\n"
            "- 以休息、补水、轻微拉伸为主\n"
            "- 若不适持续或加重，请及时就医\n"
            "等状态恢复后再继续训练计划。"
        )

    if feedback_type == "skip":
        return (
            "今天按你的反馈已跳过运动安排。\n"
            "建议至少做 8-10 分钟轻松活动（散步/拉伸）维持节奏，明天再恢复计划。"
        )

    if feedback_type == "postpone":
        pacing_plan = "你今天反馈了推迟安排，提醒强度已降低，建议晚些完成一组轻量活动即可。"

    return (
        f"运动规划：本月 {stats['count']}/{stats['goal']} 次，还差 {stats['remaining']} 次，剩余 {stats['days_left']} 天。\n"
        f"{timing}\n"
        f"{pacing_plan}\n"
        f"今日建议：{today_minutes} 分钟，重点：{focus}\n"
        f"完成后可回复“运动 {today_minutes}”。"
    )


def _within_time_window(now: datetime, start: str, end: str) -> bool:
    start_hour, start_minute = _hour_min(start)
    end_hour, end_minute = _hour_min(end)
    start_at = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_at = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start_at <= now <= end_at


def _expected_water_by_now(now: datetime, water_target_ml: int, water_start: str, water_end: str) -> int:
    start_hour, start_minute = _hour_min(water_start)
    end_hour, end_minute = _hour_min(water_end)
    start_at = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_at = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if now <= start_at:
        return 0
    if now >= end_at:
        return water_target_ml

    elapsed = (now - start_at).total_seconds()
    total = (end_at - start_at).total_seconds()
    return int(water_target_ml * elapsed / total)


def _hour_min(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


def _previous_meal_slots(meal_name: str) -> list[str]:
    if meal_name == "午餐":
        return ["早餐"]
    if meal_name == "晚餐":
        return ["早餐", "午餐"]
    return []


def _meal_reminder_prefix(meal_name: str) -> str:
    logged_slots = logged_meal_slots_today()
    previous_slots = _previous_meal_slots(meal_name)
    missing_slots = [slot for slot in previous_slots if slot not in logged_slots]
    if previous_slots and not missing_slots:
        return f"该吃{meal_name}了。你前面餐次已记录，继续保持。回复“吃了{meal_name}”记录完成。"
    if previous_slots and missing_slots:
        return f"该吃{meal_name}了。你前面还有未记录餐次（{'、'.join(missing_slots)}），如已吃可顺手补记。回复“吃了{meal_name}”记录完成。"
    return f"该吃{meal_name}了。回复“吃了{meal_name}”记录完成。"
