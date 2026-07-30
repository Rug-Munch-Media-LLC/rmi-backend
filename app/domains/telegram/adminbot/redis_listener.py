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

logger = logging.getLogger("admin_bot.redis_listener")

REDIS_QUEUE_KEY = "rag:telegram_queue"
POLL_TIMEOUT = 5  # seconds per BLPOP call; allows clean shutdown


SECRET_FILE = "/run/secrets/redis_password"


def _resolve_redis_password() -> str:
    """Resolve Redis password: env var → secret file → empty."""
    pw = os.environ.get("REDIS_PASSWORD", "")
    if pw:
        return pw
    try:
        with open(SECRET_FILE) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return ""


def _build_redis_url(base_url: str | None, password: str | None) -> str:
    """Inject password into Redis URL if provided and not already present."""
    if not base_url:
        return "redis://localhost:6379/0"
    if "@" in base_url:
        return base_url  # already has credentials
    if password and "://" in base_url:
        parts = base_url.split("://", 1)
        return f"{parts[0]}://:{password}@{parts[1]}"
    return base_url


async def start_redis_listener(bot_app) -> None:
    """Async BLPOP consumer.  Runs forever; start as an asyncio task."""
    logger.info(
        "Redis listener starting on queue=%s",
        REDIS_QUEUE_KEY,
    )

    redis_url = _build_redis_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        _resolve_redis_password(),
    )

    try:
        import redis.asyncio as aioredis

        # Force RESP2 protocol to avoid HELLO/AUTH ordering issue
        # with redis-py 8.x + DragonflyDB/Redis 7.x password auth.
        client = aioredis.from_url(redis_url, decode_responses=True, protocol=2)
        await client.ping()
    except ImportError:
        logger.warning("redis.asyncio not installed — Redis listener disabled")
        return
    except (ConnectionError, OSError, ValueError) as exc:
        logger.debug("start_redis_listener_connection_error", exc_info=True)
        logger.error("Redis connection failed: %s", exc)
        return
    except Exception as exc:
        # Catch auth errors etc.
        logger.warning("Redis setup failed (non-critical): %s", exc)
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
                    chat_id=int(os.environ.get("ADMIN_CHAT_ID", "0")),
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
