import json
import time
from typing import Any

import httpx

from app.config import settings


class FeishuClient:
    def __init__(self) -> None:
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0

    async def tenant_access_token(self) -> str:
        if self._tenant_token and time.time() < self._tenant_token_expires_at - 60:
            return self._tenant_token

        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": settings.feishu_app_id,
                    "app_secret": settings.feishu_app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu token error: {data}")
            self._tenant_token = data["tenant_access_token"]
            self._tenant_token_expires_at = time.time() + int(data.get("expire", 7200))
            return self._tenant_token

    async def send_text(self, receive_id: str, receive_id_type: str, text: str) -> None:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu send message error: {data}")


def verify_token(payload: dict[str, Any]) -> bool:
    expected = settings.feishu_verification_token
    return not expected or payload.get("token") == expected or payload.get("header", {}).get("token") == expected


def extract_message(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}

    content = message.get("content") or "{}"
    try:
        content_data = json.loads(content)
    except json.JSONDecodeError:
        content_data = {}

    text = content_data.get("text", "").strip()
    chat_id = message.get("chat_id", "")
    open_id = sender.get("sender_id", {}).get("open_id", "")

    if chat_id:
        return chat_id, "chat_id", text
    if open_id:
        return open_id, "open_id", text
    return None


feishu = FeishuClient()

