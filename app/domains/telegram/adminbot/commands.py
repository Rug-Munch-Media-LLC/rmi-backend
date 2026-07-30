"""
Admin Bot - Command Handlers
=============================
All /command handler functions for the RMI Admin Bot.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_menu,
    logs_menu,
    main_menu_keyboard,
    owner_only,
    service_action_keyboard,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ── Optional modules ───────────────────────────────────────────────
try:
    from app.domains.telegram.adminbot import admin_ai  # type: ignore[import-untyped]
except ImportError:
    admin_ai = None

# ═══════════════════════════════════════════════════════════════════
# START / HELP
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛡️ *RMI Admin Bot v3*\n\n"
        "Server management for the Rug Munch fleet.\n"
        "All commands are owner-only and logged.\n\n"
        "*Server Commands:*\n"
        "/status — System dashboard\n"
        "/disk — Disk usage report\n"
        "/mem — Memory & CPU report\n"
        "/docker — Container management\n"
        "/sh — Shell execution\n"
        "/term — Terminal mode\n\n"
        "*Service Commands:*\n"
        "/service — Service control\n"
        "/logs — View logs\n"
        "/health — Health checks\n\n"
        "*DevOps Commands:*\n"
        "/git — Git status for a repo\n"
        "/push — Push all repos\n"
        "/deploy — Deploy services\n"
        "/backup — Backup triggers\n\n"
        "*AI Commands:*\n"
        "/oc — OpenCode AI prompt\n"
        "/ai — AI model gateway\n\n"
        "*Intelligence Commands:*\n"
        "/ragstats — RAG collection sizes & embedder\n"
        "/news — Rated news articles\n"
        "/scanstats — Scanner own_data vs external\n"
        "/indexer — Chain indexer status\n\n"
        "*Misc:*\n"
        "/chain — Chain indexer status\n"
        "/alert — Send alert message\n"
        "/quick — Common ops\n"
        "/help — This message",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Gathering fleet status...")
    s = admin_ops.system_status()
    text = admin_ops.format_status(s)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                    InlineKeyboardButton("📊 Health", callback_data="alerts"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# DISK
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_disk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Checking disk usage...")
    result = admin_ops.disk_usage()
    text = admin_ops.format_disk(result)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="disk"),
                    InlineKeyboardButton("🧠 Memory", callback_data="mem"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# MEMORY / CPU
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Gathering memory report...")
    result = admin_ops.memory_report()
    text = admin_ops.format_memory(result)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="mem"),
                    InlineKeyboardButton("💾 Disk", callback_data="disk"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# DOCKER
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_docker(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🐳 *Docker Management*",
        reply_markup=docker_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# SHELL
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_shell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "💻 *Shell Execution*\n\n"
            "Usage: `/sh <command>`\n\n"
            "Examples:\n"
            "`/sh df -h`\n"
            "`/sh docker ps`\n"
            "`/sh systemctl status rmi-backend`\n\n"
            "⚠️ All commands are logged. Dangerous commands are blocked.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cmd = " ".join(args)
    msg = await update.message.reply_text(f"⏳ Running: `{cmd[:100]}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_ops.run_shell(cmd, timeout=30)
    output = result["output"] or result["error"]
    status_line = f"{'✅' if result['ok'] else '❌'} `{cmd[:80]}` ({result['duration']:.1f}s)"
    await msg.edit_text(
        f"{status_line}\n\n```\n{output[:3000]}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# SERVICES
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_service(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        text = admin_ops.service_list()
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=service_action_keyboard())
        return

    name = args[0]
    action = args[1] if len(args) > 1 else "status"

    msg = await update.message.reply_text(f"⏳ {action} {name}...")
    result = admin_ops.service_control(name, action)
    if result["ok"]:
        await msg.edit_text(
            f"✅ `{action} {name}` — done\n\n```\n{result['output'][:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=service_action_keyboard(name),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )


# ═══════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    action = args[0] if args else ""

    if action == "run":
        msg = await update.message.reply_text("⏳ Running full backup...")
        result = admin_ops.trigger_backup("full")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} Backup complete ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    elif action == "db":
        msg = await update.message.reply_text("⏳ Running database backup...")
        result = admin_ops.trigger_backup("db")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} DB Backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    else:
        await update.message.reply_text(
            "📦 *Backup Control*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Run Full Backup", callback_data="backup_run"),
                        InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db"),
                    ],
                    [InlineKeyboardButton("📊 Backup Status", callback_data="backup_status")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )


# ═══════════════════════════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🚀 *Deploy Services*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🖥️ Backend", callback_data="deploy_backend"),
                        InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot"),
                    ],
                    [
                        InlineKeyboardButton("🛡️ Admin Bot", callback_data="deploy_admin"),
                        InlineKeyboardButton("🏗️ Infra", callback_data="deploy_infra"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    msg = await update.message.reply_text(f"⏳ Deploying {service}...")
    result = admin_ops.deploy_service(service)
    await msg.edit_text(
        f"{'✅' if result['ok'] else '❌'} Deploy {service} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_button("deploy"),
    )


# ═══════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "📋 *View Logs*\n\nPick a service:",
            reply_markup=logs_menu(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    tail = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for {service}...")
    result = admin_ops.docker_logs(service, tail=tail)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📋 `{service}` (last {tail} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
    else:
        j_result = admin_ops.run_shell(f"journalctl -u {service}.service --no-pager -n {tail} 2>&1", timeout=10)
        await msg.edit_text(
            f"📋 `{service}` (systemd, last {tail} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )


# ═══════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Running health checks...")
    checks = admin_ops.health_check()
    text = admin_ops.format_health(checks)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                    InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# QUICK OPS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚡ *Quick Operations*",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Restart Backend", callback_data="docker_restart_rmi-backend"),
                    InlineKeyboardButton("🔄 Restart Bot", callback_data="docker_restart_rugmunchbot"),
                ],
                [
                    InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                    InlineKeyboardButton("📊 Health Check", callback_data="alerts"),
                ],
                [
                    InlineKeyboardButton("🐳 Docker PS", callback_data="docker_ps"),
                    InlineKeyboardButton("📦 Backup Status", callback_data="backup_status"),
                ],
                [
                    InlineKeyboardButton("💾 Disk", callback_data="disk"),
                    InlineKeyboardButton("🧠 Memory", callback_data="mem"),
                ],
                [
                    InlineKeyboardButton("📁 Git Status", callback_data="git_menu"),
                    InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
                ],
            ]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# OPENCODE / OC
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_oc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a prompt to OpenCode AI — OWNER ONLY."""
    if admin_ai is None:
        await update.message.reply_text("⚠️ AI module not available (missing admin_ai.py)")
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🤖 *OpenCode Integration*\n\n"
            "Usage:\n"
            "`/oc <prompt>` — Run any prompt through OpenCode\n"
            "`/oc task <task>` — Quick admin task\n"
            "`/oc model <model>` — View/switch model\n\n"
            "Examples:\n"
            "`/oc check disk usage and suggest cleanup`\n"
            "`/oc task restart rmi-backend and check health`\n"
            "`/oc model`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
        return

    action = args[0].lower()
    user_id = update.effective_user.id

    if action == "model":
        await update.message.reply_text(
            admin_ai.list_models(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
        return
    elif action == "task" and len(args) > 1:
        task = " ".join(args[1:])
        msg = await update.message.reply_text("🤖 Running admin task via OpenCode...")
        result = admin_ai.run_quick_task(task)
    else:
        prompt = " ".join(args)
        msg = await update.message.reply_text("🤖 Sending to OpenCode AI...")
        result = admin_ai.run_opencode(prompt, max_turns=3)

    if result["ok"]:
        output = result["output"][:3500]
        model_used = result.get("model", "unknown")
        duration = result.get("duration", 0)
        await msg.edit_text(
            f"🤖 *OpenCode* (`{model_used}`, {duration:.1f}s)\n\n```\n{output}\n```",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await msg.edit_text(
            f"❌ OpenCode Error: {result.get('error', 'unknown')[:500]}",
            parse_mode=ParseMode.MARKDOWN,
        )


# ═══════════════════════════════════════════════════════════════════
# GIT
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_git(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show git status for a repo — OWNER ONLY."""
    args = ctx.args
    if not args:
        # Show repo list menu
        buttons = []
        row = []
        for repo in admin_ops.RMI_REPOS:
            row.append(InlineKeyboardButton(repo, callback_data=f"git_{repo}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("📤 Push All", callback_data="push_all")])
        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])

        await update.message.reply_text(
            "📁 *Git Status* — Select a repo:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    repo = args[0]
    msg = await update.message.reply_text(f"⏳ Checking git status for {repo}...")
    result = admin_ops.git_status(repo)
    if result["ok"]:
        await msg.edit_text(
            result["output"][:3500],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("git_menu"),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("git_menu"),
        )


# ═══════════════════════════════════════════════════════════════════
# PUSH
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Push all repos — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Pushing all repos...")
    result = admin_ops.push_all_repos()
    output = result.get("output", "") or result.get("error", "")
    status = "✅" if result["ok"] else "❌"
    await msg.edit_text(
        f"{status} Push all repos ({result.get('duration', 0):.1f}s)\n\n```\n{output[:3000]}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_button("git_menu"),
    )


# ═══════════════════════════════════════════════════════════════════
# CHAIN
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_chain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
    """Send an alert to subscribed chats — OWNER ONLY."""
    args = ctx.args
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
        except (json.JSONDecodeError, TypeError) as e:
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
                sent_emoji = "🔥" if sentiment > 30 else "🧊" if sentiment < -30 else "➖"
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
                lines.append(f"\n*Self-Sufficiency:*")
                lines.append(
                    f"  • Questions covered: `{sufficiency.get('own_data_questions', 0)}/{sufficiency.get('total_questions', 0)}`"
                )
                lines.append(f"  • Coverage: `{sufficiency.get('own_data_pct', 0)}%`")
                dv = sufficiency.get("data_volume", {})
                if dv:
                    lines.append(f"\n*Data Volume (24h):*")
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
