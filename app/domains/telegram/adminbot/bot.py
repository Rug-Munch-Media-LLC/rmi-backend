"""
RMI Admin Bot — Fleet ChatOps
==============================
Owner-only Telegram bot for fleet ops. Extracted sub-modules:
  - helpers:      Owner gating, keyboard builders, message utilities
  - commands:     All /command handler functions
  - callbacks:    Inline keyboard callback routing
  - terminal:     Terminal mode (shell via Telegram)
  - ai_gateway:   AI model query interface
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import HTTPXRequest

from app.domains.telegram.adminbot.ai_gateway import cmd_ai
from app.domains.telegram.adminbot.callbacks import handle_callback
from app.domains.telegram.adminbot.commands import (
    cmd_admin_backups,
    cmd_admin_bot_broadcast,
    cmd_admin_bot_broadcasts,
    cmd_admin_bot_stats,
    cmd_admin_bot_status,
    cmd_admin_bot_toggle,
    cmd_admin_bot_users,
    cmd_admin_logs,
    cmd_admin_reboot,
    cmd_admin_scanner,
    cmd_alert,
    cmd_alerts,
    cmd_backup,
    cmd_backup_run,
    cmd_backup_status,
    cmd_backup_verify,
    cmd_certs,
    cmd_chain,
    cmd_cleanup,
    cmd_commands,
    cmd_deploy,
    cmd_digest,
    cmd_disk,
    cmd_dlogs,
    cmd_docker,
    cmd_dprune,
    cmd_dps,
    cmd_drestart,
    cmd_dstats,
    cmd_git,
    cmd_health,
    cmd_help,
    cmd_indexer,
    cmd_logins,
    cmd_logs,
    cmd_mem,
    cmd_menu,
    cmd_net,
    cmd_news,
    cmd_oc,
    cmd_pressure,
    cmd_push,
    cmd_quick,
    cmd_ragstats,
    cmd_repair,
    cmd_restart,
    cmd_scanstats,
    cmd_service,
    cmd_services,
    cmd_shell,
    cmd_start,
    cmd_status,
    cmd_svc,
    cmd_svc_list,
    cmd_svc_logs,
    cmd_top,
    cmd_uptime,
)
from app.domains.telegram.adminbot.config import ADMIN_BOT_TOKEN
from app.domains.telegram.adminbot.engine_gateway import cmd_ask, cmd_engine
from app.domains.telegram.adminbot.power_ops import cmd_community, cmd_heavy
from app.domains.telegram.adminbot.selftest import cmd_selftest
from app.domains.telegram.adminbot.terminal import cmd_terminal, handle_terminal_message

# ── Setup ──────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s [%(levelname)s] admin_bot: %(message)s", level=logging.INFO)
logger = logging.getLogger("admin_bot")

# Background asyncio tasks started in _post_init (kept referenced to avoid GC).
_listener_tasks: list[asyncio.Task[Any]] = []

# ═══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ═══════════════════════════════════════════════════════════════════


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uncaught exceptions from handlers."""
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
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


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════


async def _post_init(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Start the Redis listener inside the application's event loop."""
    try:
        from app.domains.telegram.adminbot.redis_listener import start_redis_listener

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
        from app.domains.telegram.adminbot.alerts_feed import start_alerts_feed

        loop = asyncio.get_event_loop()
        if loop.is_running():
            feed_task = loop.create_task(start_alerts_feed(app.bot))
        else:
            feed_task = app.create_task(start_alerts_feed(app.bot))
        # Keep a reference so the alerts feed task is not garbage collected.
        _listener_tasks.append(feed_task)
    except Exception as exc:
        logger.warning("Alerts feed setup failed (non-critical): %s", exc)


async def _post_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Cancel background listener tasks cleanly on shutdown."""
    for task in _listener_tasks:
        task.cancel()
    for task in _listener_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def main() -> None:
    """Build the Application, register handlers, start Redis listener, poll."""
    from app.domains.telegram.adminbot.config import validate_startup

    validate_startup()  # fail-closed: missing admin config exits here, not at import
    # Force IPv4 for api.telegram.org: this host's IPv6 route is broken and
    # resolves AAAA first, which caused repeated Bad Gateway / timeout errors
    # on every get_updates poll (same fix the main rugmunchbot applies in its
    # entry point). S104: binding the outbound source to 0.0.0.0 (all IPv4)
    # is intentional — it forces IPv4 and avoids the broken IPv6 route.
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")  # noqa: S104  # nosec B104 - forces IPv4 to avoid broken IPv6 route (documented above)
    request = HTTPXRequest(
        connection_pool_size=4,
        read_timeout=20,
        write_timeout=20,
        httpx_kwargs={"transport": transport},
    )
    app = (
        Application.builder()
        .token(ADMIN_BOT_TOKEN)
        .request(request)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # ── Observability (group=-2: runs before every gate) ───────────────────
    # Every incoming update leaves a journal trace so "bot did nothing" reports
    # can always be answered with evidence: did the update arrive? who sent it?
    # what did it contain? which gate consumed it? Never raises; never logs
    # message bodies (only the command entity when present).
    app.add_handler(
        TypeHandler(Update, _log_update),
        group=-2,
    )

    # ── DM-only gate (group=-1: evaluated before every group-0 handler) ──
    # Any message from a group/supergroup/channel is silently consumed here so
    # the admin surface is reachable exclusively via private chat.
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS | filters.ChatType.CHANNEL, _ignore_non_private),
        group=-1,
    )

    # ── Core Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("commands", cmd_commands))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("docker", cmd_docker))
    app.add_handler(CommandHandler("sh", cmd_shell))
    app.add_handler(CommandHandler("s", cmd_shell))  # alias
    app.add_handler(CommandHandler("service", cmd_service))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("deploy", cmd_deploy))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("repair", cmd_repair))
    app.add_handler(CommandHandler("alerts", cmd_alerts))  # legacy alias → health dashboard
    app.add_handler(CommandHandler("health", cmd_health))  # explicit health dashboard command
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("term", cmd_terminal))
    app.add_handler(CommandHandler("ai", cmd_ai))

    # ── NEW Commands v3 ──
    app.add_handler(CommandHandler("disk", cmd_disk))
    app.add_handler(CommandHandler("mem", cmd_mem))
    app.add_handler(CommandHandler("oc", cmd_oc))
    app.add_handler(CommandHandler("git", cmd_git))
    app.add_handler(CommandHandler("push", cmd_push))
    app.add_handler(CommandHandler("chain", cmd_chain))
    app.add_handler(CommandHandler("alert", cmd_alert))

    # ── NEW Commands v4 (Intelligence) ──
    app.add_handler(CommandHandler("ragstats", cmd_ragstats))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("scanstats", cmd_scanstats))
    app.add_handler(CommandHandler("indexer", cmd_indexer))

    # ── NEW Commands v4 (Server + Docker management) ──
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("uptime", cmd_uptime))
    app.add_handler(CommandHandler("net", cmd_net))
    app.add_handler(CommandHandler("certs", cmd_certs))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("dps", cmd_dps))
    app.add_handler(CommandHandler("dlogs", cmd_dlogs))
    app.add_handler(CommandHandler("dstats", cmd_dstats))
    app.add_handler(CommandHandler("dprune", cmd_dprune))
    app.add_handler(CommandHandler("drestart", cmd_drestart))

    # ── NEW Commands v6 (Service + Backup management) ──
    app.add_handler(CommandHandler("svc", cmd_svc))
    app.add_handler(CommandHandler("svc_list", cmd_svc_list))
    app.add_handler(CommandHandler("svc_logs", cmd_svc_logs))
    app.add_handler(CommandHandler("backup_status", cmd_backup_status))
    app.add_handler(CommandHandler("backup_run", cmd_backup_run))
    app.add_handler(CommandHandler("backup_verify", cmd_backup_verify))
    app.add_handler(CommandHandler("admin_logs", cmd_admin_logs))
    app.add_handler(CommandHandler("admin_svc", cmd_svc))
    app.add_handler(CommandHandler("admin_svc_list", cmd_svc_list))
    app.add_handler(CommandHandler("admin_svc_logs", cmd_svc_logs))
    app.add_handler(CommandHandler("admin_backup_status", cmd_backup_status))
    app.add_handler(CommandHandler("admin_backup_run", cmd_backup_run))
    app.add_handler(CommandHandler("admin_backup_verify", cmd_backup_verify))

    # ── NEW Commands v7 (Deep server control) ──
    app.add_handler(CommandHandler("pressure", cmd_pressure))
    app.add_handler(CommandHandler("logins", cmd_logins))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CommandHandler("digest", cmd_digest))

    # ── NEW Commands v5 (admin_* aliases + control panel) ──
    app.add_handler(CommandHandler("admin_services", cmd_services))
    app.add_handler(CommandHandler("admin_docker_ps", cmd_dps))
    app.add_handler(CommandHandler("admin_docker_logs", cmd_dlogs))
    app.add_handler(CommandHandler("admin_disk", cmd_disk))
    app.add_handler(CommandHandler("admin_memory", cmd_mem))
    app.add_handler(CommandHandler("admin_uptime", cmd_uptime))
    app.add_handler(CommandHandler("admin_backups", cmd_admin_backups))
    app.add_handler(CommandHandler("admin_rag", cmd_ragstats))
    app.add_handler(CommandHandler("admin_scanner", cmd_admin_scanner))
    app.add_handler(CommandHandler("admin_restart", cmd_restart))
    app.add_handler(CommandHandler("admin_reboot", cmd_admin_reboot))

    # ── NEW Commands v5 (bot management) ──
    app.add_handler(CommandHandler("admin_bot_stats", cmd_admin_bot_stats))
    app.add_handler(CommandHandler("admin_bot_status", cmd_admin_bot_status))
    app.add_handler(CommandHandler("admin_bot_toggle", cmd_admin_bot_toggle))
    app.add_handler(CommandHandler("admin_bot_broadcast", cmd_admin_bot_broadcast))
    app.add_handler(CommandHandler("admin_bot_broadcasts", cmd_admin_bot_broadcasts))

    # ── v8: engines + power ops (P4/P5) ──
    app.add_handler(CommandHandler("engine", cmd_engine))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("heavy", cmd_heavy))
    app.add_handler(CommandHandler("community", cmd_community))
    app.add_handler(CommandHandler("selftest", cmd_selftest))
    app.add_handler(CommandHandler("admin_bot_users", cmd_admin_bot_users))

    # Terminal mode — catch ALL text messages when in terminal
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_terminal_message),
        group=0,
    )

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Error handler
    app.add_error_handler(error_handler)

    # Natural-language chat + model picker (additive; existing handlers unchanged).
    try:
        from app.domains.telegram.adminbot.chat.register import register as register_chat

        register_chat(app)
    except Exception:
        logger.warning("chat handlers not registered", exc_info=True)
    logger.info("🛡️ RMI Admin Bot v5 starting (@rmi_admin_bot)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
