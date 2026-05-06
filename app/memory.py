import json
import logging
from datetime import date, timedelta

from app.config import settings
from app.llm import LLMError, chat_completion, llm_configured
from app.state import events_since, health_context_snapshot, remember_health_memory, rolling_health_stats


logger = logging.getLogger(__name__)


MEMORY_SUMMARY_PROMPT = """你是个人健康专家助手的长期记忆总结器。
请根据用户最近健康事件和统计，提炼可用于未来提醒和建议的长期规律。
要求：
- 输出中文，3-5 条要点。
- 关注饮水时段、饮食结构、睡眠规律、运动频率、需要干预的习惯。
- 不要做疾病诊断，不要夸大风险。
- 如果数据不足，明确写出目前数据不足，并给出下一步观察方向。
"""


async def summarize_recent_health() -> str:
    days = settings.health_memory_lookback_days
    period_end = date.today()
    period_start = period_end - timedelta(days=days - 1)

    events = events_since(days)
    stats = rolling_health_stats(days)
    if not events:
        content = f"近 {days} 天健康记录不足，暂时无法形成稳定规律。后续重点观察饮水、用餐、睡眠和运动记录。"
        remember_health_memory("daily_summary", content, period_start.isoformat(), period_end.isoformat())
        return content

    if llm_configured():
        try:
            content = await _llm_health_summary(days, events, stats)
        except LLMError as exc:
            logger.warning("LLM health memory summary failed: %s", exc)
            content = _fallback_summary(days, stats)
    else:
        content = _fallback_summary(days, stats)

    remember_health_memory("daily_summary", content, period_start.isoformat(), period_end.isoformat())
    return content


async def _llm_health_summary(days: int, events: list[dict], stats: dict) -> str:
    event_lines = [
        f"{event['created_at']} {event['event_type']} amount={event['amount']} note={event['note']}"
        for event in events[-80:]
    ]
    messages = [
        {"role": "system", "content": MEMORY_SUMMARY_PROMPT},
        {
            "role": "user",
            "content": (
                f"用户：{settings.user_name}\n"
                f"统计窗口：近 {days} 天\n"
                f"健康上下文：\n{health_context_snapshot()}\n"
                f"统计数据：{json.dumps(stats, ensure_ascii=False)}\n"
                "原始事件：\n" + "\n".join(event_lines)
            ),
        },
    ]
    return await chat_completion(messages)


def _fallback_summary(days: int, stats: dict) -> str:
    lines = [f"近 {days} 天自动总结："]
    lines.append(f"- 日均饮水约 {stats['water_avg_ml']} ml。")
    if stats["sleep_avg_hours"] is None:
        lines.append("- 睡眠记录不足，后续需要继续观察入睡和睡眠时长。")
    else:
        lines.append(f"- 平均睡眠约 {stats['sleep_avg_hours']} 小时。")
    lines.append(f"- 运动 {stats['exercise_count']} 次，共 {stats['exercise_total_minutes']} 分钟。")
    lines.append(f"- 用餐记录 {stats['meal_count']} 次，后续可继续补充食物内容以判断营养均衡。")
    return "\n".join(lines)

