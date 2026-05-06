from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.bot import handle_text
from app.channels.feishu import extract_message, feishu, verify_token
from app.channels.feishu_ws import start_feishu_ws
from app.config import settings
from app.db import init_db
from app.planner.goal_planner import ensure_today_plan_async
from app.scheduler import start_scheduler, stop_scheduler
from app.state import remember_channel


app = FastAPI(title="Health Assistant")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    await ensure_today_plan_async()
    start_scheduler()
    if settings.feishu_event_mode.lower() in {"websocket", "ws", "long_connection"}:
        start_feishu_ws()


@app.on_event("shutdown")
async def shutdown() -> None:
    stop_scheduler()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/feishu/events")
async def feishu_events(request: Request) -> dict[str, Any]:
    payload = await request.json()

    if not verify_token(payload):
        raise HTTPException(status_code=403, detail="invalid verification token")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    header = payload.get("header", {})
    if header.get("event_type") != "im.message.receive_v1":
        return {"ok": True}

    parsed = extract_message(payload)
    if not parsed:
        return {"ok": True}

    receive_id, receive_id_type, text = parsed
    remember_channel(receive_id, receive_id_type)
    reply = await handle_text(text, conversation_id=f"{receive_id_type}:{receive_id}")
    await feishu.send_text(receive_id, receive_id_type, reply)
    return {"ok": True}
