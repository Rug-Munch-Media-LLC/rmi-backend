"""
Admin Bot - Callback Query Handler
===================================
All inline keyboard callback routing for the admin bot.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops, admin_terminal
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_menu,
    is_owner,
    logs_menu,
    main_menu_keyboard,
    service_action_keyboard,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = logging.getLogger("admin_bot")

# ── Optional modules ───────────────────────────────────────────────
try:
    from app.domains.telegram.adminbot import admin_ai  # type: ignore[import-untyped]
except ImportError:
    admin_ai = None

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks to the appropriate action."""
    query = update.callback_query
    data = query.data
    await query.answer()

    if not is_owner(update):
        await query.edit_message_text("⛔ Unauthorized.")
        return

    try:
        # ── Main navigation ──
        if data == "main":
            await query.edit_message_text(
                "🛡️ *RMI Admin Bot v3* — Select an option:",
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── Status ──
        elif data == "status":
            s = admin_ops.system_status()
            text = admin_ops.format_status(s)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                         InlineKeyboardButton("📊 Health", callback_data="alerts")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Disk ──
        elif data == "disk":
            await query.edit_message_text("⏳ Checking disk usage...")
            result = admin_ops.disk_usage()
            text = admin_ops.format_disk(result)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Refresh", callback_data="disk"),
                         InlineKeyboardButton("🧠 Memory", callback_data="mem")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Memory ──
        elif data == "mem":
            await query.edit_message_text("⏳ Gathering memory report...")
            result = admin_ops.memory_report()
            text = admin_ops.format_memory(result)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Refresh", callback_data="mem"),
                         InlineKeyboardButton("💾 Disk", callback_data="disk")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Git menu ──
        elif data == "git_menu":
            from app.domains.telegram.adminbot.admin_ops import RMI_REPOS
            buttons = []
            row = []
            for repo in RMI_REPOS:
                row.append(InlineKeyboardButton(repo, callback_data=f"git_{repo}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton("📤 Push All", callback_data="push_all")])
            buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])

            await query.edit_message_text(
                "📁 *Git Status* — Select a repo:",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("git_"):
            repo = data.replace("git_", "")
            if repo == "menu":
                return
            await query.edit_message_text(f"⏳ Checking git status for {repo}...")
            result = admin_ops.git_status(repo)
            if result["ok"]:
                await query.edit_message_text(
                    result["output"][:3500],
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_button("git_menu"),
                )
            else:
                await query.edit_message_text(
                    f"❌ {result['error']}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_button("git_menu"),
                )

        # ── Push all ──
        elif data == "push_all":
            await query.edit_message_text("⏳ Pushing all repos...")
            result = admin_ops.push_all_repos()
            output = result.get("output", "") or result.get("error", "")
            status = "✅" if result["ok"] else "❌"
            await query.edit_message_text(
                f"{status} Push all repos ({result.get('duration', 0):.1f}s)\n\n```\n{output[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("git_menu"),
            )

        # ── Chain status ──
        elif data == "chain":
            await query.edit_message_text("⏳ Checking chain indexer status...")
            result = admin_ops.chain_status()
            if result["ok"]:
                await query.edit_message_text(
                    result["output"][:3500],
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("🔄 Refresh", callback_data="chain"),
                             InlineKeyboardButton("🐳 Docker", callback_data="docker")],
                            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                        ]
                    ),
                )
            else:
                await query.edit_message_text(
                    f"❌ {result['error']}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=back_button("main"),
                )

        # ── Docker ──
        elif data == "docker":
            await query.edit_message_text(
                "🐳 *Docker Management*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=docker_menu(),
            )

        elif data == "docker_ps":
            out = admin_ops.docker_ps()
            await query.edit_message_text(
                f"🐳 *Docker PS*\n\n```\n{out[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data == "docker_stats":
            result = admin_ops.docker_stats()
            await query.edit_message_text(
                f"📊 *Container Stats*\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data.startswith("docker_restart_"):
            container = data.replace("docker_restart_", "")
            result = admin_ops.docker_restart(container)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Restarted `{container}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        # ── Compose ──
        elif data == "compose_ps":
            result = admin_ops.docker_compose("ps")
            await query.edit_message_text(
                f"📋 *Compose PS*\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        elif data == "compose_restart":
            await query.edit_message_text("⏳ Restarting infra...")
            result = admin_ops.docker_compose("restart")
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Infra restarted\n\n```\n{result['output'][:2000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("docker"),
            )

        # ── Shell help ──
        elif data == "shell_help":
            await query.edit_message_text(
                "💻 *Shell Execution*\n\n"
                "Use `/sh <command>` from the chat.\n"
                "Examples: `/sh df -h`, `/sh docker ps`\n\n"
                "⚠️ Dangerous commands are blocked.\n"
                "All commands are logged.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )

        # ── Services ──
        elif data == "services":
            text = admin_ops.service_list()
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("main")
            )

        elif data.startswith("svc_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                action, svc = parts[1], parts[2]
                result = admin_ops.service_control(svc, action)
                await query.edit_message_text(
                    f"{'✅' if result['ok'] else '❌'} `{action} {svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=service_action_keyboard(svc),
                )

        # ── Backup ──
        elif data == "backup":
            await query.edit_message_text(
                "📦 *Backup Control*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Run Full Backup", callback_data="backup_run"),
                         InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db")],
                        [InlineKeyboardButton("📊 Backup Status", callback_data="backup_status")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        elif data in ("backup_run", "backup_db"):
            btype = "full" if data == "backup_run" else "db"
            await query.edit_message_text(f"⏳ Running {btype} backup...")
            result = admin_ops.trigger_backup(btype)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} {btype} backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("backup"),
            )

        elif data == "backup_status":
            result = admin_ops.trigger_backup("status")
            await query.edit_message_text(
                result["output"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("backup"),
            )

        # ── Deploy ──
        elif data == "deploy":
            await query.edit_message_text(
                "🚀 *Deploy Services*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🖥️ Backend", callback_data="deploy_backend"),
                         InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot")],
                        [InlineKeyboardButton("🛡️ Admin Bot", callback_data="deploy_admin"),
                         InlineKeyboardButton("🏗️ Infra", callback_data="deploy_infra")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        elif data.startswith("deploy_"):
            svc = data.replace("deploy_", "")
            await query.edit_message_text(f"⏳ Deploying {svc}...")
            result = admin_ops.deploy_service(svc)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Deploy {svc} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("deploy"),
            )

        # ── Logs ──
        elif data == "logs_menu":
            await query.edit_message_text(
                "📋 *View Logs* — Pick a service:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=logs_menu(),
            )

        elif data.startswith("logs_"):
            svc = data.replace("logs_", "")
            result = admin_ops.docker_logs(svc, tail=40)
            text = (
                result["output"]
                if result["ok"] and result["output"]
                else result.get("error", "no output")
            )
            await query.edit_message_text(
                f"📋 `{svc}` (last 40 lines):\n\n```\n{text[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("logs_menu"),
            )

        # ── Alerts / Health ──
        elif data == "alerts":
            checks = admin_ops.health_check()
            text = admin_ops.format_health(checks)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                         InlineKeyboardButton("🖥️ Status", callback_data="status")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Quick ──
        elif data == "quick":
            await query.edit_message_text(
                "⚡ *Quick Operations*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Restart Backend", callback_data="docker_restart_rmi-backend"),
                         InlineKeyboardButton("🔄 Restart Bot", callback_data="docker_restart_rugmunchbot")],
                        [InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                         InlineKeyboardButton("📊 Health Check", callback_data="alerts")],
                        [InlineKeyboardButton("🐳 Docker PS", callback_data="docker_ps"),
                         InlineKeyboardButton("📦 Backup Status", callback_data="backup_status")],
                        [InlineKeyboardButton("💾 Disk", callback_data="disk"),
                         InlineKeyboardButton("🧠 Memory", callback_data="mem")],
                        [InlineKeyboardButton("📁 Git Status", callback_data="git_menu"),
                         InlineKeyboardButton("📤 Push All", callback_data="push_all")],
                        [InlineKeyboardButton("💻 Terminal", callback_data="term_enter"),
                         InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )

        # ── Terminal callbacks ──
        elif data == "term_enter":
            await query.edit_message_text(
                "💻 Use `/term` to enter terminal mode from the chat.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
        elif data == "term_quick_menu":
            await query.edit_message_text(
                "⚡ *Terminal Quick Commands* — pick one to run:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_terminal.quick_commands_keyboard(),
            )
        elif data.startswith("term_quick_"):
            cmd_name = data.replace("term_quick_", "")
            result = admin_terminal.run_quick(cmd_name)
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Quick: `{cmd_name}`\n\n```\n{result['output'][:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("⚡ More Commands", callback_data="term_quick_menu")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )
        elif data == "term_exit":
            admin_terminal.exit_terminal(query.from_user.id)
            await query.edit_message_text(
                "👋 Exited terminal mode.",
                reply_markup=main_menu_keyboard(),
            )

        # ── AI callbacks ──
        elif data.startswith("ai_model_"):
            if admin_ai is None:
                await query.edit_message_text("⚠️ AI module not available.")
                return
            model_id = data.replace("ai_model_", "")
            admin_ai.set_model(query.from_user.id, model_id)
            model_name = admin_ai.MODELS.get(model_id, {}).get("name", model_id)
            await query.edit_message_text(
                f"🤖 Switched to *{model_name}*\n`{model_id}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_ai.model_keyboard(),
            )
        elif data == "ai_clear":
            if admin_ai:
                admin_ai.clear_history(query.from_user.id)
            await query.edit_message_text("🧹 Chat history cleared.", parse_mode=ParseMode.MARKDOWN)

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Callback error: {e}")
        with contextlib.suppress(httpx.HTTPError):
            await query.edit_message_text(
                f"⚠️ Error: {str(e)[:200]}", reply_markup=back_button("main")
            )
