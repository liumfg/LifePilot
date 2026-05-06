from datetime import date, datetime, time, timedelta

from app.config import settings
from app.db import db


def record_event(event_type: str, amount: float | None = None, note: str | None = None) -> int:
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO events (event_type, amount, note, created_at) VALUES (?, ?, ?, ?)",
            (event_type, amount, note, datetime.now().isoformat(timespec="seconds")),
        )
        return int(cursor.lastrowid)


def remember_channel(receive_id: str, receive_id_type: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_channels (receive_id, receive_id_type, created_at)
            VALUES (?, ?, ?)
            """,
            (receive_id, receive_id_type, datetime.now().isoformat(timespec="seconds")),
        )


def remember_message(conversation_id: str, role: str, content: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO conversation_messages (conversation_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, role, content, datetime.now().isoformat(timespec="seconds")),
        )


def recent_messages(conversation_id: str, limit: int) -> list[dict[str, str]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT role, content
            FROM conversation_messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()

    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def meal_events_between(start: datetime, end: datetime) -> list[dict[str, str | float | None]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT event_type, amount, note, created_at
            FROM events
            WHERE event_type = 'meal' AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchall()

    return [
        {
            "event_type": row["event_type"],
            "amount": row["amount"],
            "note": row["note"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def remember_meal_analysis(event_id: int, analysis: dict[str, str | int | None]) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO meal_analyses (
                event_id, meal_name, protein_level, vegetable_level, staple_level,
                oiliness, balance_score, summary, next_meal_focus, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                analysis.get("meal_name"),
                analysis.get("protein_level"),
                analysis.get("vegetable_level"),
                analysis.get("staple_level"),
                analysis.get("oiliness"),
                analysis.get("balance_score"),
                analysis.get("summary"),
                analysis.get("next_meal_focus"),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def recent_meal_analyses(days: int = 7) -> list[dict[str, str | int | None]]:
    start = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with db() as conn:
        rows = conn.execute(
            """
            SELECT ma.meal_name, ma.protein_level, ma.vegetable_level, ma.staple_level,
                   ma.oiliness, ma.balance_score, ma.summary, ma.next_meal_focus,
                   e.note, e.created_at
            FROM meal_analyses ma
            JOIN events e ON e.id = ma.event_id
            WHERE e.created_at >= ?
            ORDER BY e.created_at DESC
            LIMIT 12
            """,
            (start,),
        ).fetchall()

    return [
        {
            "meal_name": row["meal_name"],
            "protein_level": row["protein_level"],
            "vegetable_level": row["vegetable_level"],
            "staple_level": row["staple_level"],
            "oiliness": row["oiliness"],
            "balance_score": row["balance_score"],
            "summary": row["summary"],
            "next_meal_focus": row["next_meal_focus"],
            "note": row["note"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def record_menstrual_start(start_date: date | None = None, note: str | None = None) -> None:
    start = (start_date or date.today()).isoformat()
    with db() as conn:
        existing = conn.execute(
            """
            SELECT id FROM menstrual_cycles
            WHERE start_date = ?
            LIMIT 1
            """,
            (start,),
        ).fetchone()
        if existing:
            conn.execute("UPDATE menstrual_cycles SET note = COALESCE(?, note) WHERE id = ?", (note, existing["id"]))
        else:
            conn.execute(
                "INSERT INTO menstrual_cycles (start_date, end_date, note, created_at) VALUES (?, NULL, ?, ?)",
                (start, note, datetime.now().isoformat(timespec="seconds")),
            )


def record_menstrual_end(end_date: date | None = None, note: str | None = None) -> bool:
    end = (end_date or date.today()).isoformat()
    with db() as conn:
        row = conn.execute(
            """
            SELECT id FROM menstrual_cycles
            WHERE start_date IS NOT NULL AND (end_date IS NULL OR end_date = '')
            ORDER BY start_date DESC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE menstrual_cycles SET end_date = ?, note = COALESCE(?, note) WHERE id = ?", (end, note, row["id"]))
        return True


def menstrual_prediction() -> dict[str, str | int | bool | None]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT start_date, end_date, note
            FROM menstrual_cycles
            WHERE start_date IS NOT NULL
            ORDER BY start_date DESC
            LIMIT 8
            """
        ).fetchall()

    starts = [date.fromisoformat(row["start_date"]) for row in rows if row["start_date"]]
    if not starts:
        return {"has_data": False, "message": "还没有生理期记录，先回复“生理期来了”开始记录。"}

    latest = starts[0]
    intervals = [(starts[i] - starts[i + 1]).days for i in range(len(starts) - 1) if (starts[i] - starts[i + 1]).days > 0]
    cycle_days = round(sum(intervals) / len(intervals)) if intervals else 28
    next_start = latest + timedelta(days=cycle_days)
    today = date.today()
    current = rows[0]["end_date"] in (None, "") and latest <= today
    days_until = (next_start - today).days

    return {
        "has_data": True,
        "latest_start": latest.isoformat(),
        "cycle_days": cycle_days,
        "next_start": next_start.isoformat(),
        "days_until": days_until,
        "current_period": current,
    }


def remember_health_memory(
    memory_type: str,
    content: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO health_memories (memory_type, content, period_start, period_end, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (memory_type, content, period_start, period_end, datetime.now().isoformat(timespec="seconds")),
        )


def recent_health_memories(limit: int = 5) -> list[dict[str, str]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT memory_type, content, period_start, period_end, created_at
            FROM health_memories
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "memory_type": row["memory_type"],
            "content": row["content"],
            "period_start": row["period_start"] or "",
            "period_end": row["period_end"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def last_event(event_type: str) -> dict[str, str | float | None] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT event_type, amount, note, created_at
            FROM events
            WHERE event_type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (event_type,),
        ).fetchone()

    if not row:
        return None
    return {
        "event_type": row["event_type"],
        "amount": row["amount"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def events_since(days: int) -> list[dict[str, str | float | None]]:
    start = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with db() as conn:
        rows = conn.execute(
            """
            SELECT event_type, amount, note, created_at
            FROM events
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (start,),
        ).fetchall()

    return [
        {
            "event_type": row["event_type"],
            "amount": row["amount"],
            "note": row["note"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def rolling_health_stats(days: int = 7) -> dict[str, float | int | str | None]:
    events = events_since(days)
    water_total = sum(float(event["amount"] or 0) for event in events if event["event_type"] == "water")
    sleep_values = [float(event["amount"] or 0) for event in events if event["event_type"] == "sleep" and event["amount"]]
    exercise_values = [float(event["amount"] or 0) for event in events if event["event_type"] == "exercise"]
    meal_count = sum(1 for event in events if event["event_type"] == "meal")

    return {
        "days": days,
        "water_total_ml": int(water_total),
        "water_avg_ml": int(water_total / days) if days else 0,
        "sleep_avg_hours": round(sum(sleep_values) / len(sleep_values), 1) if sleep_values else None,
        "sleep_records": len(sleep_values),
        "exercise_count": len(exercise_values),
        "exercise_total_minutes": int(sum(exercise_values)),
        "meal_count": meal_count,
    }


def today_water_ml() -> int:
    today = date.today().isoformat()
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM events
            WHERE event_type = 'water' AND date(created_at) = ?
            """,
            (today,),
        ).fetchone()
    return int(row["total"])


def today_water_progress(default_goal_ml: int) -> dict[str, int]:
    plan = get_daily_plan()
    target_ml = int(plan["water_target_ml"]) if plan else default_goal_ml
    consumed_ml = today_water_ml()
    remaining_ml = max(target_ml - consumed_ml, 0)
    return {
        "target_ml": target_ml,
        "consumed_ml": consumed_ml,
        "remaining_ml": remaining_ml,
    }


def logged_meal_slots_today() -> list[str]:
    day = date.today()
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day, time(23, 59, 59))
    records = meal_events_between(day_start, day_end)
    slots: set[str] = set()
    for record in records:
        slot = _meal_slot_from_record(record)
        if slot:
            slots.add(slot)
    return sorted(slots, key=lambda name: {"早餐": 0, "午餐": 1, "晚餐": 2, "加餐": 3}.get(name, 9))


def month_exercise_count() -> int:
    month = date.today().isoformat()[:7]
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM events
            WHERE event_type = 'exercise' AND substr(created_at, 1, 7) = ?
            """,
            (month,),
        ).fetchone()
    return int(row["total"])


def recent_sleep_stats(days: int = 7) -> dict[str, float | int | str | None]:
    events = [event for event in events_since(days) if event["event_type"] == "sleep" and event["amount"]]
    values = [float(event["amount"] or 0) for event in events]
    if not values:
        return {
            "days": days,
            "records": 0,
            "avg_hours": None,
            "min_hours": None,
            "last_hours": None,
            "last_at": None,
            "short_nights": 0,
        }

    return {
        "days": days,
        "records": len(values),
        "avg_hours": round(sum(values) / len(values), 1),
        "min_hours": round(min(values), 1),
        "last_hours": round(values[-1], 1),
        "last_at": str(events[-1]["created_at"]),
        "short_nights": sum(1 for value in values if value < 7),
    }


def exercise_plan_stats() -> dict[str, int | float | str | None]:
    today = date.today()
    month = today.isoformat()[:7]
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    days_left = (next_month - today).days

    with db() as conn:
        rows = conn.execute(
            """
            SELECT amount, note, created_at
            FROM events
            WHERE event_type = 'exercise' AND substr(created_at, 1, 7) = ?
            ORDER BY created_at ASC
            """,
            (month,),
        ).fetchall()

    count = len(rows)
    remaining = max(settings.exercise_monthly_goal - count, 0)
    weeks_left = max(days_left / 7, 0.1)
    weekly_needed = remaining / weeks_left
    last = rows[-1] if rows else None
    days_since_last = None
    if last:
        last_at = datetime.fromisoformat(str(last["created_at"]))
        days_since_last = (datetime.now() - last_at).days

    return {
        "month": month,
        "goal": settings.exercise_monthly_goal,
        "count": count,
        "remaining": remaining,
        "days_left": days_left,
        "weekly_needed": round(weekly_needed, 1),
        "last_at": str(last["created_at"]) if last else None,
        "last_minutes": int(float(last["amount"] or 0)) if last else None,
        "days_since_last": days_since_last,
    }


def today_summary(water_goal_ml: int, exercise_goal: int) -> str:
    water = today_water_ml()
    exercise_count = month_exercise_count()
    return (
        f"今日状态\n"
        f"- 喝水：{water}/{water_goal_ml} ml\n"
        f"- 本月运动：{exercise_count}/{exercise_goal} 次\n"
        f"可以回复：喝了 300、吃了午餐、运动 30、睡了 7.5"
    )


def health_context_snapshot() -> str:
    stats = rolling_health_stats(7)
    sleep = recent_sleep_stats(7)
    exercise = exercise_plan_stats()
    water_last = last_event("water")
    sleep_last = last_event("sleep")
    exercise_last = last_event("exercise")
    memories = recent_health_memories(3)
    daily_plan = get_daily_plan()

    lines = [
        today_summary(settings.water_daily_goal_ml, settings.exercise_monthly_goal),
        "近 7 天统计",
        f"- 日均饮水：{stats['water_avg_ml']} ml",
        f"- 睡眠均值：{stats['sleep_avg_hours'] or '暂无'} 小时，记录 {stats['sleep_records']} 次",
        f"- 运动：{stats['exercise_count']} 次，共 {stats['exercise_total_minutes']} 分钟",
        f"- 用餐记录：{stats['meal_count']} 次",
        "睡眠规律",
        f"- 近 7 天睡眠记录 {sleep['records']} 次，平均 {sleep['avg_hours'] or '暂无'} 小时，少于 7 小时 {sleep['short_nights']} 次",
        "运动计划",
        f"- 本月目标 {exercise['goal']} 次，已完成 {exercise['count']} 次，还差 {exercise['remaining']} 次，剩余 {exercise['days_left']} 天",
    ]
    if daily_plan:
        lines.extend(
            [
                "今日执行计划",
                f"- 目标：{daily_plan['goal_text']}",
                (
                    f"- 饮水：{daily_plan['water_target_ml']} ml，"
                    f"{daily_plan['water_start']}-{daily_plan['water_end']}"
                ),
                (
                    f"- 三餐：早 {daily_plan['breakfast_time']} / "
                    f"午 {daily_plan['lunch_time']} / 晚 {daily_plan['dinner_time']}"
                ),
                f"- 睡眠：{daily_plan['sleep_time']} 前准备入睡",
                f"- 运动：{daily_plan['exercise_minutes']} 分钟，{daily_plan['exercise_focus']}",
            ]
        )
    if water_last:
        lines.append(f"- 最近喝水：{water_last['created_at']}，{int(float(water_last['amount'] or 0))} ml")
    if sleep_last:
        lines.append(f"- 最近睡眠：{sleep_last['created_at']}，{sleep_last['amount']} 小时")
    if exercise_last:
        lines.append(f"- 最近运动：{exercise_last['created_at']}，{exercise_last['amount']} 分钟")
    if memories:
        lines.append("长期记忆")
        lines.extend(f"- {memory['content']}" for memory in memories)
    return "\n".join(lines)


def _meal_slot_from_record(event: dict[str, str | float | None]) -> str | None:
    note = str(event.get("note") or "")
    if any(token in note for token in ("早餐", "早饭", "早上", "上午")):
        return "早餐"
    if any(token in note for token in ("午餐", "午饭", "中午")):
        return "午餐"
    if any(token in note for token in ("晚餐", "晚饭", "晚上")):
        return "晚餐"
    if any(token in note for token in ("加餐", "宵夜", "夜宵")):
        return "加餐"

    created_at = event.get("created_at")
    if isinstance(created_at, str):
        try:
            hour = datetime.fromisoformat(created_at).hour
        except ValueError:
            return None
        if 4 <= hour < 10:
            return "早餐"
        if 10 <= hour < 15:
            return "午餐"
        if 15 <= hour <= 23:
            return "晚餐"
    return None


def get_active_goal() -> dict[str, str] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT goal_text, constraints_text
            FROM user_goals
            WHERE active = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return {
        "goal_text": str(row["goal_text"]),
        "constraints_text": str(row["constraints_text"] or ""),
    }


def upsert_active_goal(goal_text: str, constraints_text: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        row = conn.execute(
            """
            SELECT id FROM user_goals
            WHERE active = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE user_goals
                SET goal_text = ?, constraints_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (goal_text, constraints_text, now, row["id"]),
            )
            return
        conn.execute(
            """
            INSERT INTO user_goals (goal_text, constraints_text, active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (goal_text, constraints_text, now, now),
        )


def get_daily_plan(plan_date: str | None = None) -> dict[str, str | int] | None:
    target_date = plan_date or date.today().isoformat()
    with db() as conn:
        row = conn.execute(
            """
            SELECT plan_date, goal_text, water_target_ml, water_start, water_end,
                   breakfast_time, lunch_time, dinner_time, sleep_time,
                   exercise_minutes, exercise_focus, notes, source
            FROM daily_plans
            WHERE plan_date = ?
            LIMIT 1
            """,
            (target_date,),
        ).fetchone()
    if not row:
        return None
    return {
        "plan_date": str(row["plan_date"]),
        "goal_text": str(row["goal_text"]),
        "water_target_ml": int(row["water_target_ml"]),
        "water_start": str(row["water_start"]),
        "water_end": str(row["water_end"]),
        "breakfast_time": str(row["breakfast_time"]),
        "lunch_time": str(row["lunch_time"]),
        "dinner_time": str(row["dinner_time"]),
        "sleep_time": str(row["sleep_time"]),
        "exercise_minutes": int(row["exercise_minutes"]),
        "exercise_focus": str(row["exercise_focus"] or ""),
        "notes": str(row["notes"] or ""),
        "source": str(row["source"]),
    }


def upsert_daily_plan(plan: dict[str, str | int], plan_date: str | None = None) -> None:
    target_date = plan_date or date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO daily_plans (
                plan_date, goal_text, water_target_ml, water_start, water_end,
                breakfast_time, lunch_time, dinner_time, sleep_time,
                exercise_minutes, exercise_focus, notes, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_date) DO UPDATE SET
                goal_text = excluded.goal_text,
                water_target_ml = excluded.water_target_ml,
                water_start = excluded.water_start,
                water_end = excluded.water_end,
                breakfast_time = excluded.breakfast_time,
                lunch_time = excluded.lunch_time,
                dinner_time = excluded.dinner_time,
                sleep_time = excluded.sleep_time,
                exercise_minutes = excluded.exercise_minutes,
                exercise_focus = excluded.exercise_focus,
                notes = excluded.notes,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                target_date,
                str(plan.get("goal_text") or ""),
                int(plan.get("water_target_ml") or 0),
                str(plan.get("water_start") or ""),
                str(plan.get("water_end") or ""),
                str(plan.get("breakfast_time") or ""),
                str(plan.get("lunch_time") or ""),
                str(plan.get("dinner_time") or ""),
                str(plan.get("sleep_time") or ""),
                int(plan.get("exercise_minutes") or 0),
                str(plan.get("exercise_focus") or ""),
                str(plan.get("notes") or ""),
                str(plan.get("source") or "rule"),
                now,
                now,
            ),
        )
