#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 - Module-based architecture
===================================================
Initialization, profile setup, scheduled jobs, and main entry point.
Command handlers and business logic live in handlers/ and supporting modules.

Run: cd /root/backend && python3 -m app.domains.telegram.rugmunchbot.bot
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, time as dtime

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.commands import (
    cmd_chart,
    cmd_portfolio,
    cmd_rugmap,
    cmd_simulate,
    cmd_smartmoney,
)
from app.domains.telegram.rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    BOT_TOKEN,
    CHANNELS,
    SCAM_SCHOOL_TOPICS,
    WEBSITE_URL,
    check_backend_health,
)
from app.domains.telegram.rugmunchbot.handlers.account import (
    cmd_account,
    cmd_buystars,
    cmd_pricing,
    cmd_refer,
    cmd_scamschool,
    cmd_topup,
)
from app.domains.telegram.rugmunchbot.handlers.alerts import check_alerts, cmd_alert, cmd_alerts
from app.domains.telegram.rugmunchbot.handlers.callback import handle_callback
from app.domains.telegram.rugmunchbot.handlers.error import error_handler
from app.domains.telegram.rugmunchbot.handlers.groups import (
    cmd_banuser,
    cmd_groupinfo,
    cmd_mygroup,
    cmd_voicechat,
)
from app.domains.telegram.rugmunchbot.handlers.inline import handle_inline
from app.domains.telegram.rugmunchbot.handlers.market import cmd_news, cmd_rugcheck, cmd_trending
from app.domains.telegram.rugmunchbot.handlers.message import handle_message
from app.domains.telegram.rugmunchbot.handlers.owner import (
    cmd_admin_ban,
    cmd_admin_broadcast,
    cmd_admin_set_tier,
    cmd_admin_stats,
)
from app.domains.telegram.rugmunchbot.handlers.payments import precheckout, successful_payment
from app.domains.telegram.rugmunchbot.handlers.price import cmd_dex, cmd_price
from app.domains.telegram.rugmunchbot.handlers.scanning import (
    cmd_compare,
    cmd_help,
    cmd_quick,
    cmd_scan,
    cmd_start,
    cmd_ta,
    cmd_wallet,
)
from app.domains.telegram.rugmunchbot.handlers.watchlist import (
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)

logger = get_logger("rmi_bot")


async def setup_bot_profile(app: Application) -> None:
    """Set bot description, about text, and command list via BotFather API."""
    bot = app.bot
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Bot description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set description: {e}")
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Bot short description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set short description: {e}")
    try:
        commands = [
            BotCommand("start", "Welcome & quick start guide"),
            BotCommand("help", "Full command reference"),
            BotCommand("scan", "Scan token for scams & risks"),
            BotCommand("wallet", "Wallet forensics & analysis"),
            BotCommand("ta", "Technical analysis & signals"),
            BotCommand("compare", "Compare two tokens side-by-side"),
            BotCommand("quick", "Quick token price check"),
            BotCommand("trending", "Hot tokens right now"),
            BotCommand("rugcheck", "Rug pull red flag checklist"),
            BotCommand("watch", "Add token to watchlist"),
            BotCommand("watchlist", "View your watchlist"),
            BotCommand("alerts", "Price & activity alerts"),
            BotCommand("account", "Your dashboard & usage"),
            BotCommand("pricing", "Subscription plans"),
            BotCommand("topup", "Buy extra scans (no expiry)"),
            BotCommand("buystars", "Buy Telegram Stars"),
            BotCommand("refer", "Invite friends, earn scans"),
            BotCommand("scamschool", "Learn about crypto scams"),
            BotCommand("chart", "Price chart for any token"),
            BotCommand("portfolio", "Scan wallet token holdings"),
            BotCommand("simulate", "Simulate buy/sell trade"),
            BotCommand("smartmoney", "Detect smart money wallets"),
            BotCommand("rugmap", "Wallet cluster visualization"),
            BotCommand("price", "Token price by contract address"),
            BotCommand("dex", "DEX trading pairs & liquidity data"),
            BotCommand("news", "Latest crypto news"),
            BotCommand("mygroup", "Your creator coin group"),
            BotCommand("groupinfo", "Group stats (admin)"),
            BotCommand("voicechat", "Manage voice chat (admin)"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Bot commands registered")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Could not set commands: {e}")


async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNELS.get("main"):
        return
    topic_key = random.choice(list(SCAM_SCHOOL_TOPICS.keys()))
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    sep_line = "\u2501" * 28
    text = (
        f"\U0001f6e1\ufe0f <b>Daily Scam Tip</b>\n{sep_line}\n\n"
        f"{topic['content']}\n\n"
        f"\U0001f4da Learn more: /scamschool\n"
        f"\U0001f50d Scan tokens: @RugMunchBot\n"
        f'\U0001f310 <a href="{WEBSITE_URL}">rugmunch.io</a>'
    )
    try:
        await ctx.bot.send_message(CHANNELS["main"], text, parse_mode="HTML", disable_web_page_preview=True)
    except (KeyError, TypeError) as e:
        logger.debug("daily_scam_tip_error", exc_info=True)
        logger.error(f"Daily tip failed: {e}")


def main() -> None:
    if not BOT_TOKEN:
        logger.error("RUGMUNCH_BOT_TOKEN not set!")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    # Core commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("dex", cmd_dex))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("rugcheck", cmd_rugcheck))

    # Market commands
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("news", cmd_news))

    # Account / billing
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("pricing", cmd_pricing))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("buystars", cmd_buystars))
    app.add_handler(CommandHandler("scamschool", cmd_scamschool))
    app.add_handler(CommandHandler("refer", cmd_refer))

    # Watchlist
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))

    # Alerts
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("alert", cmd_alert))

    # External command modules
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("simulate", cmd_simulate))
    app.add_handler(CommandHandler("smartmoney", cmd_smartmoney))
    app.add_handler(CommandHandler("rugmap", cmd_rugmap))

    # Owner commands
    app.add_handler(CommandHandler("admin_stats", cmd_admin_stats))
    app.add_handler(CommandHandler("admin_set_tier", cmd_admin_set_tier))
    app.add_handler(CommandHandler("admin_broadcast", cmd_admin_broadcast))
    app.add_handler(CommandHandler("admin_ban", cmd_admin_ban))

    # Group management commands
    app.add_handler(CommandHandler("mygroup", cmd_mygroup))
    app.add_handler(CommandHandler("groupinfo", cmd_groupinfo))
    app.add_handler(CommandHandler("voicechat", cmd_voicechat))
    app.add_handler(CommandHandler("banuser", cmd_banuser))

    # Inline, callbacks, messages
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Error handler
    app.add_error_handler(error_handler)

    # Scheduled jobs
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))
        job_queue.run_repeating(check_alerts, interval=ALERT_CHECK_INTERVAL, first=10)

    # Startup
    async def startup(app: Application) -> None:
        await check_backend_health()
        await setup_bot_profile(app)

    app.post_init = startup

    logger.info("\U0001f6e1\ufe0f CryptoRugMunch Bot v6 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
