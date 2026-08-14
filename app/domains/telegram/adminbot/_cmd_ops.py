"""
_cmd_ops — OC/CHAIN/ALERT/RAG/NEWS/SCANNER/INDEXER + BOT MANAGEMENT commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    bot_toggle_menu_keyboard,
    owner_only,
    refresh_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_ai
except ImportError:
    admin_ai = None  # type: ignore[assignment]  # optional module

try:
    from app.domains.telegram.adminbot import (
        admin_audit,
        admin_bot,
    )
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module
    admin_bot = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# OPENCODE / OC
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_oc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    """Send a prompt to OpenCode AI — OWNER ONLY.

    /oc runs OpenCode WITH TOOL ACCESS (agentic) — a single prompt can
    trigger shell/tool side effects. It therefore requires an explicit
    confirmation button before execution (hardened 2026-08-13).
    """
    if admin_ai is None:
        await update.message.reply_text("⚠️ AI module not available (missing admin_ai.py)")
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🤖 *OpenCode Integration*\n\n"
            "Usage:\n"
            "`/oc <prompt>` — Run any prompt through OpenCode (WITH tools)\n"
            "`/oc task <task>` — Quick admin task\n"
            "`/oc model <model>` — View/switch model\n\n"
            "Examples:\n"
            "`/oc check disk usage and suggest cleanup`\n"
            "`/oc task restart rmi-backend and check health`\n"
            "`/oc model`\n\n"
            "⚠️ `/oc` runs with tool access — you will be asked to confirm first.\n"
            "For read-only queries use `/ai ask` (no tools).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
        return

    action = args[0].lower()

    if action == "model":
        await update.message.reply_text(
            admin_ai.list_models(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
        return

    user_id = update.effective_user.id
    if action == "task" and len(args) > 1:
        task = " ".join(args[1:])
        admin_ai.stage_oc_prompt(user_id, task, task=True)
        summary = f"task: {task}"
    else:
        prompt = " ".join(args)
        admin_ai.stage_oc_prompt(user_id, prompt, task=False)
        summary = prompt

    await update.message.reply_text(
        "⚠️ *Confirm tooled OpenCode run*\n\n"
        f"`{summary[:300]}`\n\n"
        "This runs OpenCode **WITH tool access** (agentic — can execute "
        "commands / modify files). `/ai` is the read-only alternative.\n\n"
        "Confirm?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("⚠️ Yes, run with tools", callback_data="oc_confirm"),
                    InlineKeyboardButton("❌ Cancel", callback_data="oc_cancel"),
                ]
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# CHAIN
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_chain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show LiteIndexer chain status — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Checking chain indexer status...")
    result = admin_ops.chain_status()
    if result["ok"]:
        await msg.edit_text(
            result["output"][:3500],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="chain"),
                        InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )


# ═══════════════════════════════════════════════════════════════════
# ALERT
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Send an alert to subscribed chats — OWNER ONLY."""
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🔔 *Send Alert*\n\n"
            "Usage: `/alert <message>`\n"
            "Usage: `/alert critical <message>`\n\n"
            "Levels: `info` (default), `warning`, `critical`\n\n"
            "The alert will be sent to the configured admin chat.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if args[0].lower() in ("info", "warning", "critical"):
        level = args[0].lower()
        message = " ".join(args[1:])
    else:
        level = "info"
        message = " ".join(args)

    if not message:
        await update.message.reply_text("❌ Message cannot be empty.")
        return

    msg = await update.message.reply_text(f"⏳ Sending {level} alert...")
    result = admin_ops.send_alert(message, level=level)
    if result["ok"]:
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} Alert sent\n\n{result.get('output', '')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )


# ═══════════════════════════════════════════════
# RAG STATS
# ═══════════════════════════════════════════════


@owner_only
async def cmd_ragstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show RAG collection sizes and embedding model status — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Fetching RAG stats...")
    result = admin_ops.rag_stats()
    if result["ok"]:
        try:
            data = json.loads(result["output"])
            lines = ["🧠 *RAG System Stats*\n"]
            if isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, (int, float, str)):
                        lines.append(f"• `{key}`: {val}")
                    elif isinstance(val, dict):
                        lines.append(f"\n*{key}:*")
                        for k, v in val.items():
                            lines.append(f"  • `{k}`: {v}")
                    elif isinstance(val, list):
                        lines.append(f"\n*{key}:*")
                        for item in val[:20]:
                            lines.append(f"  • {item}")
            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))
        except (json.JSONDecodeError, TypeError):
            await msg.edit_text(
                f"🧠 *RAG Stats*\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
    else:
        await msg.edit_text(f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))


# ═══════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════


@owner_only
async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show latest rated news articles with bias/sentiment — OWNER ONLY."""
    limit = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 5
    msg = await update.message.reply_text("⏳ Fetching news feed...")
    result = admin_ops.news_feed(limit=limit)
    if result["ok"]:
        try:
            items = json.loads(result["output"])
            if not items:
                await msg.edit_text(
                    "📰 No news articles available.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
                )
                return
            lines = ["📰 *Rated News Feed*\n"]
            bias_emoji = {"left": "🔵", "center-left": "🔷", "center": "⚪", "center-right": "🔶", "right": "🔴"}
            for a in items[:limit]:
                title = a.get("title", "Untitled")[:80]
                source = a.get("source", "Unknown")
                source_tier = a.get("source_tier", 5)
                sentiment = a.get("sentiment_score", 0)
                bias = a.get("bias", "center")
                factual = a.get("factual_rating", "N/A")
                url = a.get("url", "")
                sent_emoji = "🔥" if sentiment > 30 else "🧊" if sentiment < -30 else "▫️"
                bias_e = bias_emoji.get(bias, "⚪")
                tier_str = "★" * max(1, 6 - source_tier)
                lines.append(
                    f"{sent_emoji} {bias_e} [{tier_str}] *{title}*\n  📰 {source} · Factuality: {factual}\n  🔗 {url}"
                )
            lines.append(f"\n{limit} articles · via RMI News Network")
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
                reply_markup=back_button("main"),
            )
        except (json.JSONDecodeError, TypeError) as e:
            await msg.edit_text(f"❌ Parse error: {e}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))
    else:
        await msg.edit_text(f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))


# ═══════════════════════════════════════════════
# SCANNER STATS
# ═══════════════════════════════════════════════


@owner_only
async def cmd_scanstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show scanner usage and own_data vs external breakdown — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Fetching scanner stats...")
    result = admin_ops.scan_stats()
    if result["ok"]:
        try:
            data = json.loads(result["output"])
            lines = ["🔍 *Scanner Statistics*\n"]
            metrics = data.get("metrics", {})
            sufficiency = data.get("sufficiency", {})

            if "error" not in metrics:
                lines.append("*Coverage (own data vs external):*")
                lines.append(f"  • Own data: `{metrics.get('own_data_pct', 'N/A')}%`")
                lines.append(f"  • DataBus: `{metrics.get('databus_pct', 'N/A')}%`")
                lines.append(f"  • External APIs: `{metrics.get('external_pct', 'N/A')}%`")
                lines.append(f"  • Total checks: `{metrics.get('total_checks', 'N/A')}`")

            if "error" not in sufficiency:
                lines.append("\n*Self-Sufficiency:*")
                lines.append(
                    f"  • Questions covered: `{sufficiency.get('own_data_questions', 0)}/{sufficiency.get('total_questions', 0)}`"
                )
                lines.append(f"  • Coverage: `{sufficiency.get('own_data_pct', 0)}%`")
                dv = sufficiency.get("data_volume", {})
                if dv:
                    lines.append("\n*Data Volume (24h):*")
                    lines.append(f"  • EVM Transfers: `{dv.get('evm_transfers_24h', 0):,}`")
                    lines.append(f"  • DEX Events: `{dv.get('dex_events_24h', 0):,}`")
                    lines.append(f"  • DEX Fill Rate: `{dv.get('dex_fill_rate_pct', 0)}%`")
                    lines.append(f"  • Freshness: `{dv.get('freshness_hours', 0)}h ago`")

            await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))
        except (json.JSONDecodeError, TypeError) as e:
            await msg.edit_text(f"❌ Parse error: {e}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))
    else:
        await msg.edit_text(f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main"))


# ═══════════════════════════════════════════════
# INDEXER STATUS
# ═══════════════════════════════════════════════


@owner_only
async def cmd_indexer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show LiteIndexer chain-by-chain status — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Checking chain indexers...")
    result = admin_ops.indexer_status()
    if result["ok"]:
        await msg.edit_text(
            result["output"][:3500],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="indexer"),
                        InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )


# ═══════════════════════════════════════════════
# v5 — CONTROL PANEL / BOT MANAGEMENT / REBOOT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_admin_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Recent admin actions from the audit trail."""
    msg = await update.message.reply_text("⏳ Reading audit log...")
    if admin_audit is None:
        await msg.edit_text("❌ admin_audit module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    result = admin_audit.recent_actions(limit=20)
    await msg.edit_text(
        admin_audit.format_admin_logs(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("admin_logs"),
    )


@owner_only
async def cmd_admin_scanner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Scanner activity from the bot DB (scans today / week / total) — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Fetching scanner stats...")
    if admin_bot is None:
        await msg.edit_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    result = admin_bot.scanner_stats()
    if result["ok"]:
        await msg.edit_text(
            result["output"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("scanstats", extra=[("📡 Backend", "scanstats")]),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )


@owner_only
async def cmd_admin_bot_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """RugMunchBot stats: users, messages today, active users — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Fetching bot stats...")
    if admin_bot is None:
        await msg.edit_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    data = await admin_bot.bot_stats()
    await msg.edit_text(
        admin_bot.format_bot_stats(data),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="bot_stats"),
                    InlineKeyboardButton("👥 Users", callback_data="bot_users"),
                ],
                [InlineKeyboardButton("🔀 Toggle", callback_data="bot_toggle_menu")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


@owner_only
async def cmd_admin_bot_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias of /admin_bot_stats — RugMunchBot status + toggle access."""
    await cmd_admin_bot_stats(update, ctx)


@owner_only
async def cmd_admin_bot_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Enable/disable RugMunchBot or its AI chat — OWNER ONLY."""
    await update.message.reply_text(
        "🔀 *RugMunchBot Toggle*\n\nChoose an action:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=bot_toggle_menu_keyboard(),
    )


@owner_only
async def cmd_admin_bot_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Broadcast a message to all RugMunchBot users — OWNER ONLY."""
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "📬 *Bot Broadcast*\n\n"
            "Usage: `/admin_bot_broadcast <message>`\n\n"
            "Sends the message to every non-banned bot user (delivered async "
            "by the bot's broadcast worker).\n\n"
            "Examples:\n"
            "`/admin_bot_broadcast 🔔 Scheduled maintenance in 1 hour`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("menu_bots"),
        )
        return
    message = " ".join(args)
    if admin_bot is None:
        await update.message.reply_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    result = await admin_bot.bot_broadcast(message)
    status = "✅" if result["ok"] else "❌"
    await update.message.reply_text(
        f"{status} {result['output'] or result['error']}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_button("menu_bots"),
    )


@owner_only
async def cmd_admin_bot_broadcasts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Recent broadcast history — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Fetching broadcast history...")
    if admin_bot is None:
        await msg.edit_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    result = await admin_bot.bot_broadcasts(limit=10)
    await msg.edit_text(
        result["output"] or result["error"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("bot_broadcasts"),
    )


@owner_only
async def cmd_admin_bot_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """List recent RugMunchBot users — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Listing users...")
    if admin_bot is None:
        await msg.edit_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        return
    result = admin_bot.bot_users(limit=15)
    await msg.edit_text(
        result["output"] or result["error"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("bot_users", extra=[("🤖 Stats", "bot_stats")]),
    )
