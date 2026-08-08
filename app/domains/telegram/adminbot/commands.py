"""
Admin Bot - Command Handlers
=============================
All /command handler functions for the RMI Admin Bot.

NOTE (degodding 2026-08-07): 936 lines. Split is deferred (all commands share the
admin-bot callback/handler plumbing). If splitting, group by feature (health /
docker / system / server) into ``commands/_groups.py``.

"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_docker, admin_health, admin_ops, admin_server
from app.domains.telegram.adminbot.helpers import (
    back_button,
    bot_toggle_menu_keyboard,
    confirm_keyboard,
    control_panel_keyboard,
    docker_menu,
    double_confirm_keyboard,
    logs_menu,
    main_menu_keyboard,
    owner_only,
    refresh_keyboard,
    service_action_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_bot  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    admin_bot = None

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
    qs = admin_health.control_panel()
    card = admin_health.format_control_panel(qs)
    await update.message.reply_text(
        f"{card}",
        reply_markup=control_panel_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


@owner_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the categorized main menu."""
    await update.message.reply_text(
        "🛡️ *RMI Admin Bot* — Select a section:",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


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
            reply_markup=back_button("main"),
        )


# ═══════════════════════════════════════════════
# NEW v4 — SERVER MANAGEMENT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Top 10 processes by CPU (or by memory with /top mem)."""
    sort_by = ctx.args[0].lower() if ctx.args and ctx.args[0].lower() in ("mem", "memory", "cpu") else "cpu"
    msg = await update.message.reply_text("⏳ Gathering processes...")
    result = admin_server.top_processes(limit=10, sort_by=sort_by)
    await msg.edit_text(
        admin_server.format_top(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("top", extra=[("🧠 By Mem", "top_mem")]),
    )


@owner_only
async def cmd_uptime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """System uptime + load averages."""
    msg = await update.message.reply_text("⏳ Checking uptime...")
    result = admin_server.uptime_report()
    await msg.edit_text(
        admin_server.format_uptime(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("uptime"),
    )


@owner_only
async def cmd_net(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Network interfaces + listening ports summary."""
    msg = await update.message.reply_text("⏳ Scanning network...")
    result = admin_server.network_report()
    await msg.edit_text(
        admin_server.format_network(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("net"),
    )


@owner_only
async def cmd_certs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """SSL certificate expiry dates (certbot + acme.sh)."""
    msg = await update.message.reply_text("⏳ Checking certificates...")
    result = admin_server.cert_expiry()
    await msg.edit_text(
        admin_server.format_certs(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("certs"),
    )


@owner_only
async def cmd_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List systemd services, optionally filtered (e.g. /services rag)."""
    filter_str = ctx.args[0] if ctx.args else ""
    msg = await update.message.reply_text("⏳ Listing services...")
    result = admin_server.services_report(filter_str=filter_str)
    extra = [("🔄 All", "services")] if filter_str else None
    await msg.edit_text(
        admin_server.format_services(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("services", extra=extra),
    )


@owner_only
async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart a systemd service — requires confirmation."""
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🔄 *Restart Service*\n\n"
            "Usage: `/restart <service>`\n\n"
            "Examples:\n"
            "`/restart rmi-backend`\n"
            "`/restart rmiadminbot`\n\n"
            "⚠️ You will be asked to confirm before the restart executes.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    name = args[0]
    if not all(c.isalnum() or c in "-_" for c in name):
        await update.message.reply_text(f"❌ Invalid service name: `{name}`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(
        f"⚠️ *Confirm restart*\n\nRestart systemd service `{name}`?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard("restart", payload=name),
    )


# ═══════════════════════════════════════════════
# NEW v4 — DOCKER MANAGEMENT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_dps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Formatted docker ps table (name, status, uptime, ports)."""
    msg = await update.message.reply_text("⏳ Listing containers...")
    result = admin_docker.docker_ps_table()
    await msg.edit_text(
        admin_docker.format_docker_ps(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("dps", extra=[("📊 Stats", "dstats")]),
    )


@owner_only
async def cmd_dlogs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tail docker logs for a container: /dlogs <container> [lines]."""
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "📜 *Docker Logs*\n\n"
            "Usage: `/dlogs <container> [lines]`\n\n"
            "Examples:\n"
            "`/dlogs rmi-backend`\n"
            "`/dlogs infinity-rag 100`\n\n"
            "Default: 50 lines. Max: 500.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    container = args[0]
    lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for `{container}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_docker.docker_logs_tail(container, lines)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📜 `{container}` (last {lines} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
    else:
        j_result = admin_ops.run_shell(f"journalctl -u {container}.service --no-pager -n {lines} 2>&1", timeout=10)
        await msg.edit_text(
            f"📜 `{container}` (systemd fallback, last {lines} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )


@owner_only
async def cmd_dstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Docker stats snapshot — top 10 by memory."""
    msg = await update.message.reply_text("⏳ Sampling container stats...")
    result = admin_docker.docker_stats_top(limit=10)
    await msg.edit_text(
        admin_docker.format_docker_stats(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("dstats", extra=[("📋 PS", "dps")]),
    )


@owner_only
async def cmd_dprune(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show docker reclaimable space with prune option."""
    msg = await update.message.reply_text("⏳ Checking docker disk usage...")
    result = admin_docker.docker_disk_usage()
    await msg.edit_text(
        admin_docker.format_docker_df(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🧹 Prune Now", callback_data="dprune"),
                    InlineKeyboardButton("📊 Stats", callback_data="dstats"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
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
    data = admin_health.health_dashboard()
    text = admin_health.format_health_dashboard(data)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard(
            "health",
            extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
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
async def cmd_admin_backups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Last backup timestamps + recent backup artifacts — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Checking backups...")
    result = admin_ops.backup_report()
    await msg.edit_text(
        result["output"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("admin_backups", extra=[("▶️ Run Backup", "backup")]),
    )


@owner_only
async def cmd_admin_scanner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
    """Enable/disable RugMunchBot or its AI chat — OWNER ONLY."""
    await update.message.reply_text(
        "🔀 *RugMunchBot Toggle*\n\nChoose an action:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=bot_toggle_menu_keyboard(),
    )


@owner_only
async def cmd_admin_bot_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all RugMunchBot users — OWNER ONLY."""
    args = ctx.args
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


@owner_only
async def cmd_admin_reboot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Reboot the server — requires DOUBLE confirmation — OWNER ONLY."""
    args = ctx.args
    delay = int(args[0]) if args and args[0].isdigit() else 60
    delay = max(10, min(delay, 600))
    await update.message.reply_text(
        f"🔄 *Server Reboot*\n\n"
        f"You are about to schedule a reboot of `{__import__('socket').gethostname()}` "
        f"in **{delay}s**.\n\n"
        f"⚠️ This will restart the backend, bots, and all services. "
        f"**Double confirmation required.**\n\n"
        f"All pending work will be interrupted. Proceed?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=double_confirm_keyboard("reboot", payload=str(delay)),
    )
