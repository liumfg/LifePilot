import asyncio
import json
import logging
import threading

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from app.bot import handle_text
from app.channels.feishu import feishu
from app.config import settings
from app.state import remember_channel


logger = logging.getLogger(__name__)
_started = False


def start_feishu_ws() -> None:
    global _started
    if _started:
        return

    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("Feishu WebSocket skipped: FEISHU_APP_ID or FEISHU_APP_SECRET is empty")
        return

    event_handler = (
        lark.EventDispatcherHandler.builder(settings.feishu_encrypt_key, settings.feishu_verification_token)
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )
    ws_client = lark.ws.Client(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True,
    )

    thread = threading.Thread(target=_run_ws_client, args=(ws_client,), name="feishu-ws-client", daemon=True)
    thread.start()
    _started = True
    logger.info("Feishu WebSocket client started")


def _run_ws_client(ws_client: lark.ws.Client) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    lark_ws_client.loop = loop
    ws_client.start()


def _on_message(data: P2ImMessageReceiveV1) -> None:
    parsed = _extract_message(data)
    if not parsed:
        return

    thread = threading.Thread(target=_process_message, args=parsed, name="feishu-message-worker", daemon=True)
    thread.start()


def _extract_message(data: P2ImMessageReceiveV1) -> tuple[str, str, str] | None:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if message is None:
        return None

    message_type = getattr(message, "message_type", "")
    if message_type and message_type != "text":
        return None

    text = _message_text(getattr(message, "content", ""))
    if not text:
        return None

    chat_id = getattr(message, "chat_id", "")
    sender_id = getattr(sender, "sender_id", None)
    open_id = getattr(sender_id, "open_id", "") if sender_id else ""

    if chat_id:
        return chat_id, "chat_id", text
    if open_id:
        return open_id, "open_id", text
    return None


def _message_text(content: str) -> str:
    try:
        data = json.loads(content or "{}")
    except json.JSONDecodeError:
        return ""

    text = data.get("text", "")
    if isinstance(text, str):
        return text.strip()
    return ""


def _process_message(receive_id: str, receive_id_type: str, text: str) -> None:
    try:
        remember_channel(receive_id, receive_id_type)
        reply = asyncio.run(handle_text(text, conversation_id=f"{receive_id_type}:{receive_id}"))
        asyncio.run(feishu.send_text(receive_id, receive_id_type, reply))
    except Exception:
        logger.exception("Failed to handle Feishu WebSocket message")
