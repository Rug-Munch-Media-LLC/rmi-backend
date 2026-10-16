"""
Admin Bot - Callback handlers: shell, services, logs & data stats
==================================================================
Shell help, systemd service control (incl. confirm/cancel flows), service
logs menus, docker logs, RAG/news/scanner/indexer stats, and the backup
report shortcut. Dispatched by ``callbacks._dispatch.handle_callback`` in
original chain order. Returns True when a branch matched.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops, admin_server
from app.domains.telegram.adminbot.helpers import (
    back_button,
    logs_menu,
    refresh_keyboard,
    service_action_keyboard,
    svc_confirm_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle shell/svc/logs/stats callback data. Returns True when matched."""
    # ── Shell help ──
    if data == "shell_help":
        await query.edit_message_text(
            "💻 *Shell Execution*\n\n"
            "Use `/sh <command>` from the chat.\n"
            "Examples: `/sh df -h`, `/sh docker ps`\n\n"
            "⚠️ READ-ONLY allowlist only (status/logs/ps/ls/cat/df/free).\n"
            "No mutations, no redirection, no pipes.\n"
            "All commands are logged.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )
        return True

    # ── Service control menu ──
    elif data == "svc_help":
        await query.edit_message_text(
            "🛠 *Service Control*\n\n"
            "Usage: `/svc <action> <service>`\n\n"
            "Actions: `start`, `stop`, `restart`\n"
            "List: `/svc_list [filter]`\n"
            "Logs: `/svc_logs <service> [lines]`\n\n"
            "⚠️ `stop`/`restart` require confirmation.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("menu_ops"),
        )
        return True
    elif data == "svc_logs_menu":
        services = [
            "rmi-backend",
            "rmiadminbot",
            "rugmunchbot",
            "chain-indexer",
            "lite-indexer",
        ]
        svc_buttons: list[list[InlineKeyboardButton]] = []
        svc_row: list[InlineKeyboardButton] = []
        for svc in services:
            svc_row.append(InlineKeyboardButton(svc, callback_data=f"svc_logs_{svc}"))
            if len(svc_row) == 2:
                svc_buttons.append(svc_row)
                svc_row = []
        if svc_row:
            svc_buttons.append(svc_row)
        svc_buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_ops")])
        await query.edit_message_text(
            "📜 *Service Logs* — pick a service:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(svc_buttons),
        )
        return True
    elif data.startswith("svc_logs_"):
        svc = data.replace("svc_logs_", "")
        result = admin_server.service_logs(svc, 50)
        text = result["output"] if result["ok"] and result["output"] else result.get("error", "no output")
        await query.edit_message_text(
            f"📜 `{svc}.service` (last 50 lines):\n\n```\n{text[:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("svc_logs_menu"),
        )
        return True

    # ── Service list (exact match — MUST precede the generic svc_* branch,
    #    which would otherwise parse "svc_list" as action="list", svc="" and
    #    silently no-op. This was a dead button until 2026-10-16.) ──
    elif data == "svc_list":
        await query.edit_message_text("⏳ Listing services...")
        result = admin_server.services_report(filter_str="")
        await query.edit_message_text(
            admin_server.format_services(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("svc_list", extra=[("📜 Logs", "svc_logs_menu")]),
        )
        return True

    # ── Services ──
    elif data.startswith("svc_") and "_confirm_" in data:
        # svc_<action>_confirm_<service> — execute the confirmed action.
        _, rest = data.split("_", 1)
        action, _, svc = rest.partition("_confirm_")
        if admin_audit is not None:
            admin_audit.log_action(f"service.{action}", svc, query.from_user.id)
        result = admin_server.service_control(svc, action)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} `{action} {svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=service_action_keyboard(svc),
        )
        return True
    elif data.startswith("svc_") and data.endswith("_cancel"):
        action = data[len("svc_") : -len("_cancel")]
        await query.edit_message_text(f"❌ `{action}` cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True
    elif data.startswith("svc_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            action, svc = parts[1], parts[2]
            # Destructive actions from inline keyboards require confirmation.
            if action in ("stop", "restart"):
                await query.edit_message_text(
                    f"⚠️ *Confirm {action}*\n\n{action} systemd service `{svc}`?",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=svc_confirm_keyboard(action, svc),
                )
            else:
                result = admin_ops.service_control(svc, action)
                await query.edit_message_text(
                    f"{'✅' if result['ok'] else '❌'} `{action} {svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=service_action_keyboard(svc),
                )
        return True

    # ── Logs ──
    elif data == "logs_menu":
        await query.edit_message_text(
            "📋 *View Logs* — Pick a service:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=logs_menu(),
        )
        return True

    elif data.startswith("logs_"):
        svc = data.replace("logs_", "")
        result = admin_ops.docker_logs(svc, tail=40)
        text = result["output"] if result["ok"] and result["output"] else result.get("error", "no output")
        await query.edit_message_text(
            f"📋 `{svc}` (last 40 lines):\n\n```\n{text[:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
        return True

    # ── RAG Stats ──
    elif data == "ragstats":
        await query.edit_message_text("⏳ Fetching RAG stats...")
        result = admin_ops.rag_stats()
        if result["ok"]:
            try:
                payload = _json.loads(result["output"])
                lines = ["🧠 *RAG System Stats*\n"]
                if isinstance(payload, dict):
                    for key, val in payload.items():
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
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("🔄 Refresh", callback_data="ragstats")],
                            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                        ]
                    ),
                )
            except Exception:
                await query.edit_message_text(
                    f"🧠 *RAG Stats*\n\n```\n{result['output'][:3000]}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_button("main"),
                )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )
        return True

    # ── News ──
    elif data == "news":
        await query.edit_message_text("⏳ Fetching news feed...")
        result = admin_ops.news_feed(limit=5)
        if result["ok"]:
            try:
                items = _json.loads(result["output"])
                if not items:
                    await query.edit_message_text(
                        "📰 No news articles available.",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=back_button("main"),
                    )
                else:
                    bias_emoji = {
                        "left": "🔵",
                        "center-left": "🔷",
                        "center": "⚪",
                        "center-right": "🔶",
                        "right": "🔴",
                    }
                    lines = ["📰 *Rated News Feed*\n"]
                    for a in items[:5]:
                        title = a.get("title", "Untitled")[:80]
                        source = a.get("source", "Unknown")
                        sentiment = a.get("sentiment_score", 0)
                        bias = a.get("bias", "center")
                        factual = a.get("factual_rating", "N/A")
                        url = a.get("url", "")
                        sent_emoji = "🔥" if sentiment > 30 else "🧊" if sentiment < -30 else "▫️"
                        bias_e = bias_emoji.get(bias, "⚪")
                        lines.append(
                            f"{sent_emoji} {bias_e} *{title}*\n  📰 {source} · Factuality: {factual}\n  🔗 {url}"
                        )
                    await query.edit_message_text(
                        "\n".join(lines),
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=False,
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton("🔄 Refresh", callback_data="news")],
                                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                            ]
                        ),
                    )
            except Exception:
                await query.edit_message_text(
                    "📰 News feed unavailable.", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
                )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )
        return True

    # ── Scanner Stats ──
    elif data == "scanstats":
        await query.edit_message_text("⏳ Fetching scanner stats...")
        result = admin_ops.scan_stats()
        if result["ok"]:
            try:
                payload = _json.loads(result["output"])
                lines = ["🔍 *Scanner Statistics*\n"]
                metrics = payload.get("metrics", {})
                sufficiency = payload.get("sufficiency", {})
                if "error" not in metrics:
                    lines.append("*Coverage:*")
                    lines.append(f"  • Own data: `{metrics.get('own_data_pct', 'N/A')}%`")
                    lines.append(f"  • DataBus: `{metrics.get('databus_pct', 'N/A')}%`")
                    lines.append(f"  • External: `{metrics.get('external_pct', 'N/A')}%`")
                if "error" not in sufficiency:
                    lines.append("\n*Sufficiency:*")
                    lines.append(
                        f"  • Questions: `{sufficiency.get('own_data_questions', 0)}/{sufficiency.get('total_questions', 0)}`"
                    )
                    lines.append(f"  • Coverage: `{sufficiency.get('own_data_pct', 0)}%`")
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("🔄 Refresh", callback_data="scanstats")],
                            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                        ]
                    ),
                )
            except Exception:
                await query.edit_message_text(
                    "🔍 Scanner stats unavailable.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_button("main"),
                )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )
        return True

    # ── Indexer ──
    elif data == "indexer":
        await query.edit_message_text("⏳ Checking chain indexers...")
        result = admin_ops.indexer_status()
        if result["ok"]:
            await query.edit_message_text(
                result["output"][:3500],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Refresh", callback_data="indexer")],
                        [InlineKeyboardButton("🐳 Docker", callback_data="docker")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )
        return True

    # ── v5: Backup report ──
    elif data == "admin_backups":
        await query.edit_message_text("⏳ Checking backups...")
        result = admin_ops.backup_report()
        await query.edit_message_text(
            result["output"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("admin_backups", extra=[("▶️ Run Backup", "backup")]),
        )
        return True

    return False
