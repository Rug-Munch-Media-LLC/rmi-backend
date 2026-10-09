"""
RMI Admin Bot — Fleet ChatOps
==============================
Owner-only Telegram bot for fleet ops. Extracted sub-modules:
  - helpers:       Owner gating, keyboard builders, message utilities
  - commands:      All /command handler functions
  - callbacks:     Inline keyboard callback routing
  - terminal:      Terminal mode (shell via Telegram)
  - ai_gateway:    AI model query interface
  - bot_setup:     Lifecycle, observability, error handling
  - bot_commands:  Command handler registration
  - bot_handlers:  Message & callback handler registration
"""

from __future__ import annotations

import logging

import httpx
from telegram.ext import (
    Application,
)
from telegram.request import HTTPXRequest

from app.domains.telegram.adminbot.bot_commands import register_canonical_commands
from app.domains.telegram.adminbot.bot_handlers import (
    register_message_handlers,
)
from app.domains.telegram.adminbot.bot_setup import (
    _post_init,
    _post_shutdown,
    register_error_handler,
    register_observability,
)
from app.domains.telegram.adminbot.config import ADMIN_BOT_TOKEN

# ── Setup ──────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s [%(levelname)s] admin_bot: %(message)s", level=logging.INFO)
logger = logging.getLogger("admin_bot")
# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════


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

    # Register observability & gate handlers
    register_observability(app)

    # Register all command handlers
    register_canonical_commands(app)

    # Register message & callback handlers
    register_message_handlers(app)

    # Error handler
    register_error_handler(app)

    # Natural-language chat + model picker (additive; existing handlers unchanged).
    try:
        from app.domains.telegram.adminbot.chat.register import register as register_chat

        register_chat(app)
    except Exception:
        logger.warning("chat handlers not registered", exc_info=True)
    logger.info("🛡️ RMI Admin Bot v5 starting (@rmi_admin_bot)...")
    # Telegram-native server menu (/menu + inline keyboard). Additive: a failure
    # here must never stop the bot from starting.
    try:
        from app.domains.telegram.adminbot.tg_menu_handlers import (
            register as register_menu,
        )

        register_menu(app)
    except Exception as exc:  # menu is optional, never fatal
        logger.warning("telegram menu not registered: %s", exc)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
