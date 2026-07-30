"""
Telegram bot admin endpoints.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import psutil
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from . import verify_admin

logger = logging.getLogger(__name__)

telegram_router = APIRouter()


@telegram_router.get("/telegram/stats")
async def telegram_stats(request: Request, _=Depends(verify_admin)) -> Any:
    """Get Telegram bot statistics."""
    bot_state_file = "/tmp/telegram_bot_state.json"
    stats = {
        "total_users": 0,
        "active_chats": 0,
        "messages_sent": 0,
        "messages_received": 0,
        "commands_processed": 0,
        "alerts_sent": 0,
        "uptime_seconds": 0,
        "status": "unknown",
    }
    if os.path.exists(bot_state_file):
        try:
            with open(bot_state_file) as f:
                stats.update(json.load(f))
        except (ValueError, OSError):
            logger.warning("Could not read bot state")
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmdline = proc.info.get("cmdline") or []
            if any("telegram" in str(c).lower() for c in cmdline):
                stats["status"] = "running"
                stats["pid"] = proc.info["pid"]
                break
        else:
            stats["status"] = "stopped"
    except (ValueError, OSError):
        stats["status"] = "unknown"
    return stats


@telegram_router.get("/telegram/users")
async def telegram_users(request: Request, _=Depends(verify_admin), limit: int = Query(50, le=200)) -> dict:
    """Get Telegram bot users."""
    users_file = "/tmp/telegram_users.json"
    users = []
    if os.path.exists(users_file):
        try:
            with open(users_file) as f:
                users = json.load(f).get("users", [])[:limit]
        except (ValueError, OSError):
            logger.warning("Could not read users file")
    return {
        "users": users,
        "total": len(users),
        "source": "telegram_bot_state",
        "status": "no_data" if not users else "available",
    }


@telegram_router.get("/telegram/messages")
async def telegram_messages(request: Request, _=Depends(verify_admin), limit: int = Query(20, le=100)) -> dict:
    """Get recent Telegram messages."""
    messages_file = "/tmp/telegram_messages.json"
    messages = []
    if os.path.exists(messages_file):
        try:
            with open(messages_file) as f:
                messages = json.load(f).get("messages", [])[:limit]
        except (ValueError, OSError):
            logger.warning("Could not read messages file")
    return {"messages": messages, "total": len(messages)}


class TelegramSendRequest(BaseModel):
    chat_id: str
    message: str


@telegram_router.post("/telegram/send")
async def telegram_send(request: Request, req: TelegramSendRequest, _=Depends(verify_admin)) -> dict[str, str]:
    """Send a message via Telegram bot."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return {"status": "failed", "error": "TELEGRAM_BOT_TOKEN not configured"}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": req.chat_id, "text": req.message, "parse_mode": "HTML"},
            )
            data = resp.json()
            if data.get("ok"):
                return {"status": "sent", "message_id": data["result"]["message_id"]}
            return {"status": "failed", "error": data.get("description", "Unknown error")}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


class TelegramBroadcastRequest(BaseModel):
    message: str


@telegram_router.post("/telegram/broadcast")
async def telegram_broadcast(
    request: Request, req: TelegramBroadcastRequest, _=Depends(verify_admin)
) -> dict[str, str]:
    """Broadcast a message to all Telegram bot users."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return {"status": "failed", "error": "TELEGRAM_BOT_TOKEN not configured"}
    users_file = "/tmp/telegram_users.json"
    chat_ids = []
    if os.path.exists(users_file):
        try:
            with open(users_file) as f:
                chat_ids = [
                    u.get("chat_id") or u.get("id")
                    for u in json.load(f).get("users", [])
                    if u.get("chat_id") or u.get("id")
                ]
        except (ValueError, OSError):
            logger.warning("Could not read users file")
    if not chat_ids:
        return {"status": "failed", "error": "No users to broadcast to"}
    sent = failed = 0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            for chat_id in chat_ids[:100]:
                try:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": req.message, "parse_mode": "HTML"},
                    )
                    if resp.json().get("ok"):
                        sent += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
    except Exception as e:
        return {"status": "partial", "sent": sent, "failed": failed, "error": str(e)}
    return {"status": "completed", "sent": sent, "failed": failed, "total": len(chat_ids)}
