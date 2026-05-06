from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.channels.feishu import feishu
from app.config import settings
from app.memory import summarize_recent_health
from app.reminders.messages import exercise_reminder, meal_reminder_async, sleep_reminder, water_reminder


scheduler = AsyncIOScheduler(timezone=settings.app_timezone)


async def send_default(text: str) -> None:
    if not settings.feishu_default_receive_id:
        return
    await feishu.send_text(
        settings.feishu_default_receive_id,
        settings.feishu_default_receive_id_type,
        text,
    )


async def send_water_reminder() -> None:
    reminder = water_reminder()
    if reminder:
        await send_default(reminder)


async def send_meal_reminder(meal_name: str) -> None:
    await send_default(await meal_reminder_async(meal_name))


async def send_sleep_reminder() -> None:
    await send_default(sleep_reminder())


async def send_exercise_reminder() -> None:
    await send_default(exercise_reminder())


async def send_health_memory_summary() -> None:
    content = await summarize_recent_health()
    await send_default(f"今日健康复盘\n\n{content}")


def _hour_min(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


def setup_scheduler() -> None:
    start_hour, _ = _hour_min(settings.water_remind_start)
    end_hour, _ = _hour_min(settings.water_remind_end)

    scheduler.add_job(
        send_water_reminder,
        CronTrigger(hour=f"{start_hour}-{end_hour}", minute=f"*/{settings.water_remind_check_minutes}"),
        id="water",
        replace_existing=True,
    )

    for job_id, meal_name, when in [
        ("breakfast", "早餐", settings.meal_breakfast_time),
        ("lunch", "午餐", settings.meal_lunch_time),
        ("dinner", "晚餐", settings.meal_dinner_time),
    ]:
        hour, minute = _hour_min(when)
        scheduler.add_job(
            send_meal_reminder,
            CronTrigger(hour=hour, minute=minute),
            args=[meal_name],
            id=job_id,
            replace_existing=True,
        )

    sleep_hour, sleep_minute = _hour_min(settings.sleep_time)
    scheduler.add_job(
        send_sleep_reminder,
        CronTrigger(hour=sleep_hour, minute=sleep_minute),
        id="sleep",
        replace_existing=True,
    )

    scheduler.add_job(
        send_exercise_reminder,
        CronTrigger(day_of_week="mon,wed,fri", hour=19, minute=30),
        id="exercise",
        replace_existing=True,
    )

    summary_hour, summary_minute = _hour_min(settings.health_memory_summary_time)
    scheduler.add_job(
        send_health_memory_summary,
        CronTrigger(hour=summary_hour, minute=summary_minute),
        id="health_memory_summary",
        replace_existing=True,
    )


def start_scheduler() -> None:
    setup_scheduler()
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
