import json
import logging
import re

from app.llm import LLMError, chat_completion, llm_configured


logger = logging.getLogger(__name__)


MEAL_ANALYSIS_PROMPT = """你是饮食记录结构化分析器。
请把用户的一餐记录解析成严格 JSON，不要输出 Markdown 或解释。
JSON 格式：
{
  "meal_name": "早餐|午餐|晚餐|加餐|未知",
  "protein_level": "missing|low|ok|high|unknown",
  "vegetable_level": "missing|low|ok|high|unknown",
  "staple_level": "missing|low|ok|high|unknown",
  "oiliness": "low|medium|high|unknown",
  "balance_score": 0 到 100 的整数,
  "summary": "一句话总结这餐结构",
  "next_meal_focus": "下一餐最应该调整的一句话"
}
判断要保守：不知道就填 unknown，不要编造用户没说的食物。
"""


async def analyze_meal(note: str) -> dict[str, str | int | None] | None:
    if not llm_configured():
        return None

    messages = [
        {"role": "system", "content": MEAL_ANALYSIS_PROMPT},
        {"role": "user", "content": note},
    ]
    try:
        raw = await chat_completion(messages)
        data = _parse_json_object(raw)
    except LLMError as exc:
        logger.warning("Meal analysis failed: %s", exc)
        return None

    score = data.get("balance_score")
    if not isinstance(score, int):
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = None

    return {
        "meal_name": _text(data.get("meal_name")),
        "protein_level": _text(data.get("protein_level")),
        "vegetable_level": _text(data.get("vegetable_level")),
        "staple_level": _text(data.get("staple_level")),
        "oiliness": _text(data.get("oiliness")),
        "balance_score": score,
        "summary": _text(data.get("summary")),
        "next_meal_focus": _text(data.get("next_meal_focus")),
    }


def _parse_json_object(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise LLMError("LLM did not return JSON")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise LLMError("LLM JSON is not an object")
    return data


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
