#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 - Module-based architecture
===================================================
Initialization, profile setup, scheduled jobs, and main entry point.
Command handlers and business logic live in handlers/ and supporting modules.

v5: world-class menu (/start categories), freestyle AI chat (/chat), and the
admin control panel (/admin, /admin_stats, /admin_toggle*, /admin_user,
/admin_set_tier, /admin_ban, /admin_unban, /admin_broadcast, /admin_logs).

Run: cd /root/backend && python3 -m app.domains.telegram.rugmunchbot.bot
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, time as dtime
from functools import wraps

import httpx
from telegram import BotCommand, Update
from telegram.error import TelegramError
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
from telegram.request import HTTPXRequest

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.admin_state import track_command
from app.domains.telegram.rugmunchbot.alert_worker import deliver_pending_alerts
from app.domains.telegram.rugmunchbot.broadcast_worker import deliver_pending_broadcasts
from app.domains.telegram.rugmunchbot.commands import (
    cmd_chart,
    cmd_events,
    cmd_portfolio,
    cmd_rugmap,
    cmd_simulate,
    cmd_smartmoney,
)
from app.domains.telegram.rugmunchbot.commands.premium import (
    cmd_cluster,
    cmd_deathclock,
    cmd_impersonation,
    cmd_launches,
    cmd_mev,
)
from app.domains.telegram.rugmunchbot.commands.premium_extra import cmd_whale
from app.domains.telegram.rugmunchbot.commands.premium_v2 import (
    cmd_honeypot,
    cmd_laundry,
    cmd_lphealth,
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
from app.domains.telegram.rugmunchbot.handlers.api_access import cmd_api
from app.domains.telegram.rugmunchbot.handlers.ask import cmd_ask
from app.domains.telegram.rugmunchbot.handlers.callback import handle_callback
from app.domains.telegram.rugmunchbot.handlers.chat import cmd_chat
from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
from app.domains.telegram.rugmunchbot.handlers.defi import register_defi
from app.domains.telegram.rugmunchbot.handlers.error import error_handler
from app.domains.telegram.rugmunchbot.handlers.groups import (
    cmd_banuser,
    cmd_groupinfo,
    cmd_mygroup,
    cmd_voicechat,
)
from app.domains.telegram.rugmunchbot.handlers.holders import cmd_holders
from app.domains.telegram.rugmunchbot.handlers.inline import handle_inline
from app.domains.telegram.rugmunchbot.handlers.investigate import cmd_investigate
from app.domains.telegram.rugmunchbot.handlers.market import cmd_news, cmd_rugcheck, register_market
from app.domains.telegram.rugmunchbot.handlers.owner import (
    cmd_admin,
    cmd_admin_ban,
    cmd_admin_broadcast,
    cmd_admin_logs,
    cmd_admin_set_tier,
    cmd_admin_stats,
    cmd_admin_toggle,
    cmd_admin_toggle_chat,
    cmd_admin_unban,
    cmd_admin_user,
)
from app.domains.telegram.rugmunchbot.handlers.payments import precheckout, successful_payment
from app.domains.telegram.rugmunchbot.handlers.prediction import register_prediction
from app.domains.telegram.rugmunchbot.handlers.price import cmd_dex, cmd_price
from app.domains.telegram.rugmunchbot.handlers.report import cmd_report
from app.domains.telegram.rugmunchbot.handlers.research import cmd_research
from app.domains.telegram.rugmunchbot.handlers.scanning import (
    cmd_compare,
    cmd_deepscan,
    cmd_deployer,
    cmd_help,
    cmd_hype,
    cmd_quick,
    cmd_scan,
    cmd_start,
    cmd_ta,
    cmd_wallet,
    sentinel_scan,
)
from app.domains.telegram.rugmunchbot.handlers.sentiment import cmd_sentiment
from app.domains.telegram.rugmunchbot.handlers.trace import cmd_trace, register_trace_commands
from app.domains.telegram.rugmunchbot.handlers.watchlist import (
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)
from app.domains.telegram.rugmunchbot.handlers.webhook import cmd_webhook
from app.domains.telegram.rugmunchbot.states import is_owner

logger = get_logger("rmi_bot")


def _reg(app: Application, name: str, handler) -> None:
    """Register a command with per-command activity tracking (for /admin_stats).

    Tracks into the Redis-backed fleet analytics (admin_state.track_command,
    shared with the backend admin API) AND the local SQLite daily stats.
    """

    @wraps(handler)
    async def tracked(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        db.record_activity(cmd=True)
        try:
            await track_command(name)
        except Exception:
            logger.debug("admin_state track_command failed", exc_info=True)
        await handler(update, ctx)

    app.add_handler(CommandHandler(name, tracked))


def install_maintenance_gate(app: Application) -> None:
    """Globally disable the bot via /admin_toggle (maintenance mode).

    Wraps ``Application.process_update`` so every non-owner update is dropped
    (with a maintenance notice) while the bot is disabled. Reads the Redis
    shared state (admin_state.is_bot_enabled) so the backend admin API and
    the bot agree; fails open (bot enabled) when Redis is down.
    Owner updates always pass through.
    """
    from app.domains.telegram.rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            try:
                enabled = await admin_state.is_bot_enabled()
            except Exception:
                enabled = True
            if not enabled:
                effective_message = getattr(update, "effective_message", None)
                callback_query = getattr(update, "callback_query", None)
                if effective_message is not None:
                    try:
                        await effective_message.reply_text(
                            "\U0001f527 <b>RugMunch is under maintenance</b>\n\n"
                            "We're upgrading the intelligence pipeline \u2014 check back soon!\n"
                            "\U0001f6e1\ufe0f rugmunch.io",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.debug("maintenance notice send failed", exc_info=True)
                elif callback_query is not None:
                    try:
                        await callback_query.answer("Bot is in maintenance mode.", show_alert=True)
                    except Exception:
                        logger.debug("maintenance callback answer failed", exc_info=True)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded


async def _send_ban_notice(update: object) -> None:
    """Send the single banned-user notice on whatever update type arrived."""
    effective_message = getattr(update, "effective_message", None)
    callback_query = getattr(update, "callback_query", None)
    inline_query = getattr(update, "inline_query", None)
    if effective_message is not None:
        try:
            await effective_message.reply_text(
                "\U0001f6ab <b>You are banned from using this bot.</b>",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("ban notice send failed", exc_info=True)
    elif callback_query is not None:
        try:
            await callback_query.answer("You are banned from using this bot.", show_alert=True)
        except Exception:
            logger.debug("ban callback answer failed", exc_info=True)
    elif inline_query is not None:
        try:
            await inline_query.answer([], cache_time=60, is_personal=True)
        except Exception:
            logger.debug("ban inline answer failed", exc_info=True)


def install_ban_gate(app: Application) -> None:
    """Block banned users from EVERY command, callback, inline and message.

    Wraps ``Application.process_update`` so the ban check runs before any
    handler is dispatched — commands, callback queries, inline queries,
    pre-checkout and free text all pass through this single gate.

    Consults BOTH authoritative stores:
      * local SQLite ``users.is_banned`` (written by /admin_ban, /admin_unban
        and the admin bot),
      * the Redis set ``bot:admin:banned_users`` (written by the backend admin
        API via ``admin_state.ban_user``).
    Fails open (allows) when a store is unavailable. Owners are always exempt
    so they can still reach /admin_unban. Banned users receive a single
    "You are banned" notice and the update is dropped — no command executes.
    """
    from app.domains.telegram.rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            banned = False
            try:
                banned = await admin_state.is_user_banned(effective_user.id)
            except Exception:
                logger.debug("admin_state:is_user_banned failed", exc_info=True)
            if not banned:
                try:
                    banned = db.is_user_banned(effective_user.id)
                except Exception:
                    logger.debug("db:is_user_banned failed", exc_info=True)
            if banned:
                logger.info("ban-gate blocked user=%s", effective_user.id)
                await _send_ban_notice(update)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded


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
            BotCommand("scan", "Quick token scan for scams & risks"),
            BotCommand("deepscan", "Full multi-phase deep token scan"),
            BotCommand("sentinelscan", "SENTINEL deep forensic scan (fast)"),
            BotCommand("deployer", "Deployer fingerprint check"),
            BotCommand("hype", "Quick 0-100 hype score"),
            BotCommand("wallet", "Wallet forensics & analysis"),
            BotCommand("ta", "Technical analysis & signals"),
            BotCommand("compare", "Compare two tokens side-by-side"),
            BotCommand("quick", "Quick token price check"),
            BotCommand("trending", "Trending tokens by chain (e.g. /trending solana)"),
            BotCommand("rugcheck", "Rug pull red flag checklist"),
            BotCommand("watch", "Add token to watchlist"),
            BotCommand("watchlist", "View your watchlist"),
            BotCommand("alerts", "Price & activity alerts"),
            BotCommand("sentiment", "Market sentiment & fear/greed index"),
            BotCommand("research", "RAG-powered deep research (Hunter+)"),
            BotCommand("ask", "Deep AI answers from RAG + LLM"),
            BotCommand("chat", "Freestyle AI chat — crypto, DeFi & code"),
            BotCommand("account", "Your dashboard & usage"),
            BotCommand("pricing", "Subscription plans"),
            BotCommand("topup", "Buy extra scans (no expiry)"),
            BotCommand("buystars", "Buy Telegram Stars"),
            BotCommand("refer", "Invite friends, earn scans"),
            BotCommand("scamschool", "Learn about crypto scams"),
            BotCommand("chart", "Chart image for any token"),
            BotCommand("portfolio", "Scan wallet token holdings"),
            BotCommand("mev", "MEV risk advisory & safe swap params"),
            BotCommand("impersonation", "Detect fake tokens & impersonation"),
            BotCommand("deathclock", "Token longevity prediction"),
            BotCommand("launches", "New token launches with risk flags"),
            BotCommand("cluster", "Wallet clustering & funding paths"),
            BotCommand("whale", "Whale movement alert subscriptions"),
            BotCommand("honeypot", "Honeypot detector - can you sell?"),
            BotCommand("lphealth", "Liquidity pool health check"),
            BotCommand("laundry", "Money laundering detector"),
            BotCommand("simulate", "Simulate buy/sell trade"),
            BotCommand("smartmoney", "Detect smart money wallets"),
            BotCommand("rugmap", "RugMap bubble map image"),
            BotCommand("price", "Token price by contract address"),
            BotCommand("dex", "DEX trading pairs & liquidity data"),
            BotCommand("holders", "Top holders & concentration (Scout+)"),
            BotCommand("trace", "Cross-chain fund flow trace (Hunter+)"),
            BotCommand("investigate", "Deep investigation report (Pro)"),
            BotCommand("webhook", "Custom webhooks management (Pro)"),
            BotCommand("api", "API access token (Pro)"),
            BotCommand("news", "Latest crypto news"),
            BotCommand("events", "News events & their price impact"),
            BotCommand("report", "Report a scam address"),
            BotCommand("mygroup", "Your creator coin group"),
            BotCommand("groupinfo", "Group stats (admin)"),
            BotCommand("voicechat", "Manage voice chat (admin)"),
            BotCommand("market", "Market overview — BTC/ETH, Fear & Greed"),
            BotCommand("movers", "Biggest gainers & losers"),
            BotCommand("whales", "Whale movement alerts"),
            BotCommand("polymarket", "Polymarket prediction markets"),
            BotCommand("election", "Election odds & predictions"),
            BotCommand("odds", "Sports & event odds"),
            BotCommand("yield", "DeFi yield farming opportunities"),
            BotCommand("lpcheck", "Liquidity pool health check"),
            BotCommand("defi", "DeFi protocol analytics"),
            BotCommand("dumps", "Detect token dump patterns"),
            BotCommand("flow", "Money flow & fund tracking"),
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
    except TelegramError as e:
        # Channel perms / transient API error — log and let the next daily run retry.
        logger.error(f"Daily tip failed: {e}", exc_info=True)


def main() -> None:
    if not BOT_TOKEN:
        logger.error("RUGMUNCH_BOT_TOKEN not set!")
        sys.exit(1)

    # Force IPv4 for api.telegram.org: this host's IPv6 route is broken and
    # resolves AAAA first, which caused 20+ ConnectTimeouts per 3h and made
    # every outbound call fall through to the global error handler.
    # PTB 21+ drops the `transport` kwarg in favor of `httpx_kwargs`.
    # S104: binding the outbound source to 0.0.0.0 (all IPv4) is intentional —
    # it forces IPv4 and avoids the broken IPv6 route.
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")  # noqa: S104
    request = HTTPXRequest(httpx_kwargs={"transport": transport})

    app = Application.builder().token(BOT_TOKEN).request(request).build()

    # Core commands
    _reg(app, "start", cmd_start)
    _reg(app, "help", cmd_help)
    _reg(app, "price", cmd_price)
    _reg(app, "dex", cmd_dex)
    _reg(app, "scan", cmd_scan)
    _reg(app, "deepscan", cmd_deepscan)
    _reg(app, "sentinelscan", sentinel_scan)
    _reg(app, "deployer", cmd_deployer)
    _reg(app, "hype", cmd_hype)
    _reg(app, "wallet", cmd_wallet)
    _reg(app, "ta", cmd_ta)
    _reg(app, "compare", cmd_compare)
    _reg(app, "quick", cmd_quick)
    _reg(app, "rugcheck", cmd_rugcheck)

    # Anti-abuse
    _reg(app, "report", cmd_report)

    # Market commands
    register_market(app)  # /market /trending /movers /launches /whales
    register_prediction(app)  # /polymarket /election /odds
    register_defi(app)  # /yield /lpcheck /defi
    _reg(app, "news", cmd_news)
    _reg(app, "sentiment", cmd_sentiment)

    # Research / RAG / freestyle chat
    _reg(app, "research", cmd_research)
    _reg(app, "ask", cmd_ask)
    _reg(app, "chat", cmd_chat)

    # Account / billing
    _reg(app, "account", cmd_account)
    _reg(app, "pricing", cmd_pricing)
    _reg(app, "topup", cmd_topup)
    _reg(app, "buystars", cmd_buystars)
    _reg(app, "scamschool", cmd_scamschool)
    _reg(app, "refer", cmd_refer)

    # Watchlist
    _reg(app, "watchlist", cmd_watchlist)
    _reg(app, "watch", cmd_watch)
    _reg(app, "unwatch", cmd_unwatch)

    # Alerts
    _reg(app, "alerts", cmd_alerts)
    _reg(app, "alert", cmd_alert)

    # External command modules
    _reg(app, "chart", cmd_chart)
    _reg(app, "portfolio", cmd_portfolio)
    _reg(app, "simulate", cmd_simulate)
    _reg(app, "smartmoney", cmd_smartmoney)
    _reg(app, "rugmap", cmd_rugmap)
    _reg(app, "events", cmd_events)

    # Premium intelligence commands (all free during beta)
    _reg(app, "mev", cmd_mev)
    _reg(app, "impersonation", cmd_impersonation)
    _reg(app, "deathclock", cmd_deathclock)
    _reg(app, "launches", cmd_launches)
    _reg(app, "cluster", cmd_cluster)
    _reg(app, "whale", cmd_whale)
    _reg(app, "honeypot", cmd_honeypot)
    _reg(app, "lphealth", cmd_lphealth)
    _reg(app, "laundry", cmd_laundry)

    # Owner / admin panel (owner-only — hidden from BotFather list)
    _reg(app, "admin", cmd_admin)
    _reg(app, "admin_stats", cmd_admin_stats)
    _reg(app, "admin_set_tier", cmd_admin_set_tier)
    _reg(app, "admin_broadcast", cmd_admin_broadcast)
    _reg(app, "admin_toggle", cmd_admin_toggle)
    _reg(app, "admin_toggle_chat", cmd_admin_toggle_chat)
    _reg(app, "admin_user", cmd_admin_user)
    _reg(app, "admin_ban", cmd_admin_ban)
    _reg(app, "admin_unban", cmd_admin_unban)
    _reg(app, "admin_logs", cmd_admin_logs)

    # Premium commands
    _reg(app, "holders", cmd_holders)
    _reg(app, "trace", cmd_trace)
    register_trace_commands(app)  # /dumps /flow
    _reg(app, "investigate", cmd_investigate)
    _reg(app, "webhook", cmd_webhook)
    _reg(app, "api", cmd_api)

    # Group management commands
    _reg(app, "mygroup", cmd_mygroup)
    _reg(app, "groupinfo", cmd_groupinfo)
    _reg(app, "voicechat", cmd_voicechat)
    _reg(app, "banuser", cmd_banuser)

    # Inline, callbacks, messages
    app.add_handler(InlineQueryHandler(handle_inline))
    app.add_handler(CallbackQueryHandler(handle_callback))
    # SINGLE free-text pipeline (merged from the old handle_message + chat_ai):
    # $TOKEN lookup, bare-address auto-scan, known-scam block, owner admin
    # console, then backend RAG chat with news_v2 grounding. There must be NO
    # other MessageHandler(filters.TEXT & ~filters.COMMAND) registered —
    # PTB dispatches to the first matching handler and a second registration
    # would shadow features (the old handle_message shadow bug).
    register_chat_ai(app)

    # Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # Error handler
    app.add_error_handler(error_handler)

    # Global maintenance gate (/admin_toggle off)
    install_maintenance_gate(app)

    # Global ban gate: banned users are blocked at every entry point before
    # any handler runs. Installed AFTER the maintenance gate so the ban check
    # is the outermost wrapper and takes priority (banned users get the ban
    # notice even during maintenance). Owners are exempt in both gates.
    install_ban_gate(app)

    # Scheduled jobs
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))
        job_queue.run_repeating(check_alerts, interval=ALERT_CHECK_INTERVAL, first=10)
        # Admin-panel broadcasts (queued in Redis by the backend API)
        job_queue.run_repeating(deliver_pending_broadcasts, interval=30, first=15)
        # Intelligent webhook alerts (queued in Redis by the backend webhook
        # engine → telegram_bridge; whale/price/liquidity/launch push alerts)
        job_queue.run_repeating(deliver_pending_alerts, interval=30, first=15)

    # Startup
    async def startup(app: Application) -> None:
        await check_backend_health()
        await setup_bot_profile(app)

    app.post_init = startup

    logger.info("\U0001f6e1\ufe0f CryptoRugMunch Bot v6 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
