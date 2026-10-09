"""
Bot setup & lifecycle helpers — extracted from bot.py for structure gate.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.domains.telegram.adminbot.alerts_feed import start_alerts_feed
from app.domains.telegram.adminbot.bot_handlers import publish_command_menu
from app.domains.telegram.adminbot.redis_listener import start_redis_listener

logger = logging.getLogger("admin_bot.setup")

# Background asyncio tasks started in _post_init (kept referenced to avoid GC).
_listener_tasks: list[asyncio.Task[Any]] = []


async def error_handler(update: Any, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uncaught exceptions from handlers."""
    logger.error("Exception: %s", ctx.error, exc_info=ctx.error)
    if hasattr(update, "effective_message") and update.effective_message:
        with contextlib.suppress(ConnectionError, OSError, TimeoutError):
            await update.effective_message.reply_text(f"⚠️ Operation failed: {str(ctx.error)[:200]}")


async def _ignore_non_private(update: Any, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """DM-only defense-in-depth: silently drop group/channel message updates.

    Registered in handler group -1 (checked before every group-0 handler), so
    commands sent from groups/channels never reach the owner-only surface.
    """
    return None


async def _log_update(update: Any, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Update-level observability (group -2: runs BEFORE every gate).

    Every incoming update leaves a journal trace (`journalctl -t rmiadminbot`)
    so a "bot did nothing" report can always be answered with evidence:
    did the update arrive? who sent it? what did it contain? which gate
    consumed it? Never raises; never logs message BODIES (only the command
    entity when present) so chat content stays out of the journal.
    """
    try:
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        cb = getattr(update, "callback_query", None)
        msg = getattr(update, "message", None)
        who = user.id if user is not None else "?"
        where = chat.type if chat is not None else "?"
        if cb is not None:
            logger.info("update cb data=%s user=%s chat=%s", cb.data, who, where)
        elif msg is not None:
            text = getattr(msg, "text", None) or ""
            command = text.split()[0][:32] if text.startswith("/") else f"<text:{len(text)}>"
            logger.info("update msg %s user=%s chat=%s", command, who, where)
        else:
            logger.info("update other type=%s user=%s chat=%s", getattr(update, "update_id", "?"), who, where)
    except Exception:  # observability must never break update processing
        logger.debug("update logging failed", exc_info=True)


async def _post_init(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Start the Redis listener inside the application's event loop."""
    await publish_command_menu(app)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = loop.create_task(start_redis_listener(app.bot))
        else:
            task = app.create_task(start_redis_listener(app.bot))
        # Keep a reference so the background listener is not garbage collected.
        _listener_tasks.append(task)
    except Exception as exc:
        logger.warning("Redis listener setup failed (non-critical): %s", exc)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            feed_task = loop.create_task(start_alerts_feed(app.bot))
        else:
            feed_task = app.create_task(start_alerts_feed(app.bot))
        # Keep a reference so the alerts feed task is not garbage collected.
        _listener_tasks.append(feed_task)
    except Exception as exc:
        logger.warning("Alerts feed setup failed (non-critical): %s", exc)

    # Health + migration notifications (additive; producers isolate their own failures).
    try:
        from app.domains.telegram.adminbot.notify.watch import watch as watch_notifications

        loop = asyncio.get_event_loop()
        task = loop.create_task(watch_notifications()) if loop.is_running() else app.create_task(watch_notifications())
        _listener_tasks.append(task)
    except Exception as exc:
        logger.warning("Notification watcher setup failed (non-critical): %s", exc)


async def _post_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Cancel background listener tasks cleanly on shutdown."""
    for task in _listener_tasks:
        task.cancel()
    for task in _listener_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def register_observability(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register observability and gate handlers."""
    # ── Observability (group=-2: runs before every gate) ───────────────────
    app.add_handler(
        TypeHandler(Update, _log_update),
        group=-2,
    )

    # ── DM-only gate (group=-1: evaluated before every group-0 handler) ──
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS | filters.ChatType.CHANNEL, _ignore_non_private),
        group=-1,
    )


def register_error_handler(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the global error handler."""
    app.add_error_handler(error_handler)
