"""
Redis alert consumer for the RMI Admin Bot.

async BLPOP on ``rag:telegram_queue``; each message is forwarded to
the configured ``ADMIN_CHAT_ID`` via the running Telegram bot instance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID

logger = logging.getLogger("admin_bot.redis_listener")

REDIS_QUEUE_KEY = "rag:telegram_queue"
POLL_TIMEOUT = 5  # seconds per BLPOP call; allows clean shutdown

async def start_redis_listener(bot_app) -> None:
    """Async BLPOP consumer.  Runs forever; start as an asyncio task."""
    logger.info(
        "Redis listener starting on queue=%s → chat=%s",
        REDIS_QUEUE_KEY,
        ADMIN_CHAT_ID,
    )

    try:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        if redis_password and "://" in redis_url:
            # Inject password into URL if needed
            if "@" not in redis_url:
                parts = redis_url.split("://", 1)
                redis_url = f"{parts[0]}://:{redis_password}@{parts[1]}"
        client = aioredis.from_url(redis_url)
        await client.ping()
    except (ConnectionError, OSError, ValueError) as exc :
        logger.debug("start_redis_listener_error", exc_info=True)
        logger.error("Redis connection failed: %s", exc)
        return

    _empty_logged = False

    try:
        while True:
            try:
                result = await client.blpop(REDIS_QUEUE_KEY, timeout=POLL_TIMEOUT)
                if result is None:
                    if not _empty_logged:
                        logger.info(
                            "Redis queue '%s' is empty — waiting for messages",
                            REDIS_QUEUE_KEY,
                        )
                        _empty_logged = True
                    continue  # timeout, loop again

                _empty_logged = False
                _key, raw = result
                payload = json.loads(raw)
                text = _format_payload(payload)

                await bot_app.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=text,
                    parse_mode="Markdown",
                )
            except json.JSONDecodeError:
                logger.warning("Non-JSON message on queue, skipping")
            except (ConnectionError, OSError, TimeoutError) as exc:
                logger.error("BLPOP/forward error: %s", exc)
    except asyncio.CancelledError:
        logger.info("Redis listener cancelled")
    finally:
        await client.aclose()

def _format_payload(payload: dict) -> str:
    """Format a RAG alert payload into a concise Telegram message."""
    level = payload.get("level", "INFO").upper()
    emoji = {"CRITICAL": "🚨", "ERROR": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}.get(level, "ℹ️")
    title = payload.get("title", "RAG Alert")
    body = payload.get("body", payload.get("message", ""))
    source = payload.get("source", "")
    ts = payload.get("timestamp", "")

    lines = [f"{emoji} *{title}*"]
    if body:
        lines.append(f"\n{body[:500]}")
    if source:
        lines.append(f"\n📡 `{source}`")
    if ts:
        lines.append(f"\n🕐 `{ts}`")
    return "\n".join(lines) + "\n\n_rag:telegram_queue_"
