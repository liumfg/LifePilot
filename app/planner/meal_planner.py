from datetime import date, datetime, time, timedelta

from app.llm import LLMError, chat_completion, llm_configured
from app.state import meal_events_between, menstrual_prediction, recent_meal_analyses


MENUS = [
    ("燕麦牛奶 + 鸡蛋 + 苹果", "米饭 + 番茄牛肉 + 清炒西兰花", "杂粮饭 + 豆腐菌菇汤 + 生菜"),
    ("全麦面包 + 酸奶 + 香蕉", "米饭 + 香菇鸡腿 + 菠菜", "红薯 + 清蒸鱼 + 西红柿鸡蛋汤"),
    ("小米粥 + 鸡蛋 + 橙子", "杂粮饭 + 豆腐青菜 + 胡萝卜炒肉", "荞麦面 + 青菜 + 虾仁"),
    ("玉米 + 牛奶 + 坚果", "米饭 + 青椒牛肉 + 紫甘蓝", "南瓜粥 + 鸡胸肉沙拉"),
    ("豆浆 + 包子 + 奇异果", "面条 + 鸡蛋 + 青菜", "米饭 + 菌菇豆腐 + 油麦菜"),
    ("酸奶燕麦杯 + 鸡蛋", "杂粮饭 + 清蒸鱼 + 芦笋", "玉米 + 番茄豆腐汤 + 生菜"),
    ("粥 + 鸡蛋 + 蓝莓", "米饭 + 土豆炖牛肉 + 西兰花", "全麦卷饼 + 鸡胸肉 + 彩椒"),
]

MEAL_INDEX = {
    "早餐": 0,
    "午餐": 1,
    "晚餐": 2,
}

MEAL_WINDOWS = {
    "早餐": (time(4, 0), time(10, 30)),
    "午餐": (time(10, 30), time(15, 30)),
    "晚餐": (time(15, 30), time(23, 59, 59)),
}

HEAVY_KEYWORDS = ("炸", "煎", "油", "辣", "火锅", "烧烤", "汉堡", "薯条", "肥肉", "奶茶", "甜品", "蛋糕")
PROTEIN_KEYWORDS = (
    "蛋",
    "鸡",
    "鱼",
    "虾",
    "牛",
    "猪",
    "肉",
    "豆腐",
    "豆浆",
    "牛奶",
    "酸奶",
    "奶",
    "蛋白",
)
VEGETABLE_KEYWORDS = (
    "菜",
    "青菜",
    "菠菜",
    "生菜",
    "西兰花",
    "胡萝卜",
    "番茄",
    "西红柿",
    "菌菇",
    "蘑菇",
    "芦笋",
    "彩椒",
    "紫甘蓝",
)
STAPLE_KEYWORDS = ("饭", "面", "粥", "包子", "馒头", "面包", "燕麦", "玉米", "红薯", "土豆", "南瓜", "卷饼")


def menu_for_day(day: date | None = None) -> str:
    day = day or date.today()
    breakfast, lunch, dinner = MENUS[day.toordinal() % len(MENUS)]
    return (
        "今日菜单建议\n"
        f"- 早餐：{breakfast}\n"
        f"- 午餐：{lunch}\n"
        f"- 晚餐：{dinner}\n"
        "原则：主食、优质蛋白、蔬菜都要有；一周内轮换肉、鱼、蛋、豆制品。"
    )


def get_menu(meal_name: str | None = None, day: date | None = None, strategy: str = "adaptive") -> str:
    day = day or date.today()
    if not meal_name:
        return menu_for_day(day)
    if strategy == "template":
        meal_index = MEAL_INDEX.get(meal_name)
        if meal_index is None:
            return menu_for_day(day)
        return f"{meal_name}模板建议：{MENUS[day.toordinal() % len(MENUS)][meal_index]}"
    return menu_for_meal(meal_name, day)


async def get_menu_async(meal_name: str | None = None, day: date | None = None, strategy: str = "adaptive") -> str:
    day = day or date.today()
    if not meal_name:
        return menu_for_day(day)
    if strategy == "template":
        return get_menu(meal_name=meal_name, day=day, strategy="template")
    return await menu_for_meal_llm(meal_name, day)


def menu_for_meal(meal_name: str, day: date | None = None) -> str:
    day = day or date.today()
    meal_index = MEAL_INDEX.get(meal_name)
    if meal_index is None:
        return menu_for_day(day)

    profile = _meal_profile(day, meal_name)
    base_suggestion = _adaptive_suggestion(day, meal_name, meal_index, profile)
    focus = _meal_focus(profile, meal_name)
    analysis_focus = _analysis_focus()
    period_focus = _period_focus()
    recorded = _recorded_summary(profile["today_before"])

    lines = [
        f"{meal_name}建议：{base_suggestion}",
        f"调整重点：{focus}",
    ]
    if analysis_focus:
        lines.append(f"近期饮食：{analysis_focus}")
    if period_focus:
        lines.append(f"生理期关注：{period_focus}")
    if recorded:
        lines.append(f"今天已记录：{recorded}")
    elif meal_name == "早餐":
        lines.append("今天还没有饮食记录；如果已经吃过，可以回复具体吃了什么，我会用于后续搭配。")
    else:
        lines.append("今天前面餐次还没有记录；如果已经吃过，可以回复具体吃了什么，我会用于后续搭配。")
    lines.append("搭配原则：主食适量，优先补足优质蛋白和蔬菜；上一餐偏油时，下一餐尽量清淡。")
    return "\n".join(lines)


async def menu_for_meal_llm(meal_name: str, day: date | None = None) -> str:
    day = day or date.today()
    fallback = menu_for_meal(meal_name, day)
    meal_index = MEAL_INDEX.get(meal_name)
    if meal_index is None or not llm_configured():
        return fallback

    profile = _meal_profile(day, meal_name)
    template = MENUS[day.toordinal() % len(MENUS)][meal_index]
    focus = _meal_focus(profile, meal_name)
    analysis_focus = _analysis_focus()
    period_focus = _period_focus()
    recorded = _recorded_summary(profile["today_before"]) or "无"

    prompt = (
        "请基于以下上下文，生成一条个性化单餐建议。\n"
        f"餐次：{meal_name}\n"
        f"模板建议：{template}\n"
        f"今天前面餐次记录：{recorded}\n"
        f"调整重点：{focus}\n"
        f"近期饮食分析：{analysis_focus or '无'}\n"
        f"经期关注：{period_focus or '无'}\n"
        "输出要求：\n"
        "1) 严格输出 3-5 行纯文本。\n"
        f"2) 第一行必须是“{meal_name}建议：<具体菜品组合>”。\n"
        "3) 第二行必须是“调整重点：<一句话>”。\n"
        "4) 可以包含“今天已记录：...”或“搭配原则：...”。\n"
        "5) 不要输出免责声明，不要输出 JSON。"
    )
    try:
        content = await chat_completion(
            [
                {"role": "system", "content": "你是中文营养搭配助手，擅长根据当日已吃内容给出下一餐可执行建议。"},
                {"role": "user", "content": prompt},
            ]
        )
    except LLMError:
        return fallback

    cleaned = content.strip()
    if not cleaned or not cleaned.startswith(f"{meal_name}建议："):
        return fallback
    return cleaned


def _meal_profile(day: date, meal_name: str) -> dict[str, list[dict[str, str | float | None]]]:
    day_start = datetime.combine(day, time.min)
    day_end = datetime.combine(day, time(23, 59, 59))
    today_window_start = day_start
    meal_window = MEAL_WINDOWS.get(meal_name)
    if meal_window:
        today_window_start = datetime.combine(day, meal_window[0])
    day_before = day_start - timedelta(days=1)
    today_records = meal_events_between(day_start, day_end)
    previous_slots = _previous_meal_slots(meal_name)
    today_before = [
        event
        for event in today_records
        if _belongs_to_previous_meals(event, previous_slots, today_window_start)
    ]

    return {
        "today_before": today_before,
        "yesterday": meal_events_between(day_before, day_start),
    }


def _meal_focus(profile: dict[str, list[dict[str, str | float | None]]], meal_name: str) -> str:
    today_notes = _notes(profile["today_before"])
    yesterday_notes = _notes(profile["yesterday"])
    recent_notes = today_notes + " " + yesterday_notes

    if _has_any(yesterday_notes, HEAVY_KEYWORDS):
        return "昨天饮食可能偏油，今天这餐建议选择清蒸、炖煮、少油做法，蔬菜占到半盘左右。"

    if meal_name == "早餐":
        return "早餐建议保证一份优质蛋白和适量主食，比如鸡蛋、豆浆、牛奶、酸奶搭配燕麦、面包或玉米。"

    if meal_name in {"午餐", "晚餐"} and today_notes and not _has_any(today_notes, PROTEIN_KEYWORDS):
        return "今天前面记录里蛋白质不明显，这餐建议补一份鸡蛋、鱼虾、鸡肉、牛肉或豆制品。"

    if meal_name in {"午餐", "晚餐"} and not today_notes:
        return "今天前面餐次没有记录，建议这餐保证一份优质蛋白，避免只吃主食。"

    if recent_notes and not _has_any(recent_notes, VEGETABLE_KEYWORDS):
        return "近期记录里蔬菜不明显，这餐建议至少加一份深色蔬菜。"

    if recent_notes and not _has_any(recent_notes, STAPLE_KEYWORDS):
        return "近期主食记录不明显，这餐可以安排少量米饭、杂粮、面或薯类，保证能量稳定。"

    return "按正常均衡搭配即可，保持主食、优质蛋白、蔬菜都有。"


def _analysis_focus() -> str:
    analyses = recent_meal_analyses(3)
    if not analyses:
        return ""

    latest = analyses[0]
    if latest.get("next_meal_focus"):
        return str(latest["next_meal_focus"])

    if any(item.get("oiliness") == "high" for item in analyses[:3]):
        return "最近有偏油记录，下一餐优先清淡少油。"
    if any(item.get("vegetable_level") in {"missing", "low"} for item in analyses[:3]):
        return "最近蔬菜偏少，下一餐优先补一份深色蔬菜。"
    if any(item.get("protein_level") in {"missing", "low"} for item in analyses[:3]):
        return "最近蛋白质偏少，下一餐补一份蛋、奶、鱼虾、瘦肉或豆制品。"
    return ""


def _period_focus() -> str:
    prediction = menstrual_prediction()
    if not prediction.get("has_data"):
        return ""

    if prediction.get("current_period"):
        return "当前可能处于生理期，建议保证优质蛋白，搭配温热、易消化食物，少冰冷刺激。"

    days_until = prediction.get("days_until")
    if isinstance(days_until, int) and 0 <= days_until <= 3:
        return f"预计约 {days_until} 天后进入生理期，接下来注意规律吃饭，提前补充蛋白质和铁含量较高的食物。"
    return ""


def _recorded_summary(events: list[dict[str, str | float | None]]) -> str:
    notes = [str(event["note"]).strip() for event in events if event.get("note")]
    return "；".join(notes[-3:])


def _notes(events: list[dict[str, str | float | None]]) -> str:
    return " ".join(str(event["note"] or "") for event in events)


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _adaptive_suggestion(
    day: date,
    meal_name: str,
    meal_index: int,
    profile: dict[str, list[dict[str, str | float | None]]],
) -> str:
    template = MENUS[day.toordinal() % len(MENUS)][meal_index]
    today_notes = _notes(profile["today_before"])
    yesterday_notes = _notes(profile["yesterday"])
    recent = f"{today_notes} {yesterday_notes}"

    if meal_name == "晚餐":
        if _has_any(today_notes, HEAVY_KEYWORDS):
            return "糙米饭 + 清蒸鱼 + 清炒西兰花"
        if _has_any(today_notes, STAPLE_KEYWORDS) and not _has_any(today_notes, PROTEIN_KEYWORDS):
            return "藜麦饭 + 番茄鸡胸肉 + 蒜蓉生菜"
        if _has_any(today_notes, PROTEIN_KEYWORDS) and not _has_any(today_notes, VEGETABLE_KEYWORDS):
            return "杂粮饭 + 香菇豆腐煲 + 菠菜"
        if _has_any(recent, "奶茶 甜品 蛋糕".split()):
            return "玉米 + 虾仁豆腐汤 + 凉拌黄瓜"

    if meal_name == "午餐":
        if _has_any(yesterday_notes, HEAVY_KEYWORDS):
            return "米饭 + 清炖牛腩 + 蒜蓉油麦菜"
        if not _has_any(today_notes, PROTEIN_KEYWORDS):
            return "米饭 + 香煎鸡胸肉 + 西红柿炒蛋"

    if meal_name == "早餐":
        if _has_any(yesterday_notes, HEAVY_KEYWORDS):
            return "燕麦粥 + 水煮蛋 + 苹果"
        return "全麦面包 + 无糖酸奶 + 鸡蛋"

    return template


def _previous_meal_slots(meal_name: str) -> set[str]:
    if meal_name == "午餐":
        return {"早餐"}
    if meal_name == "晚餐":
        return {"早餐", "午餐"}
    return set()


def _belongs_to_previous_meals(event: dict[str, str | float | None], previous_slots: set[str], window_start: datetime) -> bool:
    slot = _meal_slot_from_event(event)
    if slot and slot in previous_slots:
        return True
    created_at = event.get("created_at")
    if isinstance(created_at, str):
        try:
            return datetime.fromisoformat(created_at) < window_start
        except ValueError:
            return False
    return False


def _meal_slot_from_event(event: dict[str, str | float | None]) -> str | None:
    note = str(event.get("note") or "")
    if any(token in note for token in ("早餐", "早饭", "早上", "上午")):
        return "早餐"
    if any(token in note for token in ("午餐", "午饭", "中午")):
        return "午餐"
    if any(token in note for token in ("晚餐", "晚饭", "晚上")):
        return "晚餐"
    return None
