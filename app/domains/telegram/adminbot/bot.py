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

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.domains.telegram.adminbot.ai_gateway import cmd_ai
from app.domains.telegram.adminbot.callbacks import handle_callback
from app.domains.telegram.adminbot.commands import (
    cmd_alert,
    cmd_alerts,
    cmd_backup,
    cmd_chain,
    cmd_deploy,
    cmd_disk,
    cmd_docker,
    cmd_git,
    cmd_help,
    cmd_logs,
    cmd_mem,
    cmd_oc,
    cmd_push,
    cmd_quick,
    cmd_service,
    cmd_shell,
    cmd_start,
    cmd_status,
)
from app.domains.telegram.adminbot.config import ADMIN_BOT_TOKEN
from app.domains.telegram.adminbot.terminal import cmd_terminal, handle_terminal_message

# ── Setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] admin_bot: %(message)s", level=logging.INFO
)
logger = logging.getLogger("admin_bot")

# ═══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ═══════════════════════════════════════════════════════════════════

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uncaught exceptions from handlers."""
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
    if hasattr(update, "effective_message") and update.effective_message:
        with contextlib.suppress(ConnectionError, OSError, TimeoutError):
            await update.effective_message.reply_text(
                f"⚠️ Operation failed: {str(ctx.error)[:200]}"
            )

# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

async def _post_init(app: Application) -> None:
    """Start the Redis listener inside the application's event loop."""
    try:
        from app.domains.telegram.adminbot.redis_listener import start_redis_listener

        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(start_redis_listener(app.bot))
        else:
            app.create_task(start_redis_listener(app.bot))
    except Exception as exc:
        logger.warning("Redis listener setup failed (non-critical): %s", exc)

def main() -> None:
    """Build the Application, register handlers, start Redis listener, poll."""
    app = Application.builder().token(ADMIN_BOT_TOKEN).post_init(_post_init).build()

    # ── Core Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("docker", cmd_docker))
    app.add_handler(CommandHandler("sh", cmd_shell))
    app.add_handler(CommandHandler("s", cmd_shell))          # alias
    app.add_handler(CommandHandler("service", cmd_service))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("deploy", cmd_deploy))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("health", cmd_alerts))    # alias
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

    # Terminal mode — catch ALL text messages when in terminal
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_terminal_message),
        group=0,
    )

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("🛡️ RMI Admin Bot v3 starting (@rmi_admin_bot)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
