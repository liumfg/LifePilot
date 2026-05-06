from typing import Any

import httpx

from app.config import settings


class LLMError(RuntimeError):
    pass


SYSTEM_PROMPT = """你是一个中文个人健康专家助手。
你的目标是帮助用户建立长期健康习惯，包括饮水、饮食、睡眠、运动和日常状态复盘。
你可以基于已有记录给出生活方式建议，但不能做疾病诊断、处方、用药指导或替代医生。
如果用户描述明显的急性、严重或持续症状，应建议及时就医。
回复要简洁、具体、可执行，优先使用中文。
"""


def llm_configured() -> bool:
    return settings.llm_enabled and bool(settings.llm_api_key and settings.llm_base_url and settings.llm_model)


async def chat_completion(messages: list[dict[str, str]]) -> str:
    if not llm_configured():
        raise LLMError("LLM is not configured")

    api_style = settings.llm_api_style.strip().lower()
    if api_style == "responses":
        return await _responses_completion(messages)
    if api_style == "chat_completions":
        return await _chat_completions(messages)

    raise LLMError(f"Unsupported LLM API style: {settings.llm_api_style}")


async def _chat_completions(messages: list[dict[str, str]]) -> str:
    base_url = settings.llm_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.4,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"LLM request failed: {exc}; body={resp.text[:500]}") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM returned empty content")

    return content.strip()


async def _responses_completion(messages: list[dict[str, str]]) -> str:
    base_url = settings.llm_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "input": [_to_response_message(message) for message in messages],
        "temperature": 0.4,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{base_url}/responses",
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        content = _extract_responses_text(data)
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"LLM request failed: {exc}; body={resp.text[:500]}") from exc
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    if not content:
        raise LLMError("LLM returned empty content")

    return content


def _to_response_message(message: dict[str, str]) -> dict[str, Any]:
    return {
        "role": message["role"],
        "content": [{"type": "input_text", "text": message["content"]}],
    }


def _extract_responses_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()

    parts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)

    return "\n".join(part.strip() for part in parts if part.strip())
