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

from app.domains.telegram.adminbot import admin_docker, admin_health, admin_ops, admin_server, admin_terminal
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_menu,
    is_owner,
    logs_menu,
    main_menu_keyboard,
    refresh_keyboard,
    section_menu_keyboard,
    section_title,
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
            qs = admin_health.quick_status()
            card = admin_health.format_quick_status(qs)
            await query.edit_message_text(
                f"🛡️ *RMI Admin Bot v4* — Fleet ChatOps\n\n{card}\n\n_Select a section:_",
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── Section sub-menus ──
        elif data.startswith("menu_"):
            section = data.replace("menu_", "")
            await query.edit_message_text(
                f"{section_title(section)} — select an action:",
                reply_markup=section_menu_keyboard(section),
                parse_mode=ParseMode.MARKDOWN,
            )

        # ── Quick status card ──
        elif data == "quick_status":
            qs = admin_health.quick_status()
            text = admin_health.format_quick_status(qs)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard(
                    "quick_status",
                    extra=[("🚨 Full Health", "health"), ("🖥️ Status", "status")],
                ),
            )

        # ── Health dashboard ──
        elif data == "health":
            await query.edit_message_text("⏳ Running health checks...")
            data_dict = admin_health.health_dashboard()
            text = admin_health.format_health_dashboard(data_dict)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard(
                    "health",
                    extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
                ),
            )

        # ── v4: Top processes ──
        elif data == "top":
            await query.edit_message_text("⏳ Gathering processes...")
            result = admin_server.top_processes(limit=10, sort_by="cpu")
            await query.edit_message_text(
                admin_server.format_top(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("top", extra=[("🧠 By Mem", "top_mem")]),
            )
        elif data == "top_mem":
            await query.edit_message_text("⏳ Gathering processes...")
            result = admin_server.top_processes(limit=10, sort_by="mem")
            await query.edit_message_text(
                admin_server.format_top(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("top_mem", extra=[("🔥 By CPU", "top")]),
            )

        # ── v4: Uptime ──
        elif data == "uptime":
            await query.edit_message_text("⏳ Checking uptime...")
            result = admin_server.uptime_report()
            await query.edit_message_text(
                admin_server.format_uptime(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("uptime"),
            )

        # ── v4: Network ──
        elif data == "net":
            await query.edit_message_text("⏳ Scanning network...")
            result = admin_server.network_report()
            await query.edit_message_text(
                admin_server.format_network(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("net"),
            )

        # ── v4: SSL certs ──
        elif data == "certs":
            await query.edit_message_text("⏳ Checking certificates...")
            result = admin_server.cert_expiry()
            await query.edit_message_text(
                admin_server.format_certs(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("certs"),
            )

        # ── v4: Services ──
        elif data == "services":
            await query.edit_message_text("⏳ Listing services...")
            result = admin_server.services_report(filter_str="")
            await query.edit_message_text(
                admin_server.format_services(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("services"),
            )

        # ── v4: Restart service (confirmation flow) ──
        elif data.startswith("restart_confirm_"):
            svc = data.replace("restart_confirm_", "")
            result = admin_server.restart_service(svc)
            status_emoji = "✅" if result["ok"] else "❌"
            await query.edit_message_text(
                f"{status_emoji} Restarted `{svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("services", extra=[("🔧 Services", "services")]),
            )
        elif data.startswith("restart_cancel"):
            await query.edit_message_text("❌ Restart cancelled.", parse_mode=ParseMode.MARKDOWN)

        # ── v4: Docker PS ──
        elif data == "dps":
            await query.edit_message_text("⏳ Listing containers...")
            result = admin_docker.docker_ps_table()
            await query.edit_message_text(
                admin_docker.format_docker_ps(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("dps", extra=[("📊 Stats", "dstats")]),
            )

        # ── v4: Docker stats ──
        elif data == "dstats":
            await query.edit_message_text("⏳ Sampling container stats...")
            result = admin_docker.docker_stats_top(limit=10)
            await query.edit_message_text(
                admin_docker.format_docker_stats(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("dstats", extra=[("📋 PS", "dps")]),
            )

        # ── v4: Docker disk usage / prune (confirmation flow) ──
        elif data == "dprune":
            result = admin_docker.docker_disk_usage()
            await query.edit_message_text(
                admin_docker.format_docker_df(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🧹 Prune Now", callback_data="dprune_confirm"),
                            InlineKeyboardButton("❌ Cancel", callback_data="dprune_cancel"),
                        ]
                    ]
                ),
            )
        elif data == "dprune_confirm":
            await query.edit_message_text(
                "⚠️ *FINAL CONFIRMATION*\n\n"
                "This runs `docker system prune -af --volumes`.\n"
                "It removes ALL unused images, containers, volumes and build cache.\n\n"
                "Proceed?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅ Yes, Prune", callback_data="dprune_run"),
                            InlineKeyboardButton("❌ Cancel", callback_data="dprune_cancel"),
                        ]
                    ]
                ),
            )
        elif data == "dprune_run":
            await query.edit_message_text("🧹 Pruning docker...")
            result = admin_docker.docker_prune()
            await query.edit_message_text(
                f"{'✅' if result['ok'] else '❌'} Prune complete\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("dprune"),
            )
        elif data.startswith("dprune_cancel"):
            await query.edit_message_text("❌ Prune cancelled.", parse_mode=ParseMode.MARKDOWN)

        # ── v4: AI section helpers ──
        elif data == "oc_help":
            from app.domains.telegram.adminbot import admin_ai as _admin_ai

            kb = _admin_ai.model_keyboard() if _admin_ai and hasattr(_admin_ai, "model_keyboard") else None
            await query.edit_message_text(
                "🤖 *OpenCode Integration*\n\n"
                "`/oc <prompt>` — Run any prompt through OpenCode\n"
                "`/oc task <task>` — Quick admin task\n"
                "`/oc model` — View/switch model",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
            )
        elif data == "ai_menu":
            if admin_ai is None:
                await query.edit_message_text("⚠️ AI module not available.", parse_mode=ParseMode.MARKDOWN)
            else:
                await query.edit_message_text(
                    admin_ai.list_models(),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=admin_ai.model_keyboard(),
                )
        elif data == "alert_help":
            await query.edit_message_text(
                "🔔 *Send Alert*\n\n"
                "Usage: `/alert <message>`\n"
                "Usage: `/alert critical <message>`\n\n"
                "Levels: `info` (default), `warning`, `critical`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("menu_ops"),
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
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                            InlineKeyboardButton("📊 Health", callback_data="alerts"),
                        ],
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
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="disk"),
                            InlineKeyboardButton("🧠 Memory", callback_data="mem"),
                        ],
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
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="mem"),
                            InlineKeyboardButton("💾 Disk", callback_data="disk"),
                        ],
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
                            [
                                InlineKeyboardButton("🔄 Refresh", callback_data="chain"),
                                InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                            ],
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
                        [
                            InlineKeyboardButton("🔄 Run Full Backup", callback_data="backup_run"),
                            InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db"),
                        ],
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
            text = result["output"] if result["ok"] and result["output"] else result.get("error", "no output")
            await query.edit_message_text(
                f"📋 `{svc}` (last 40 lines):\n\n```\n{text[:3000]}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("logs_menu"),
            )

        # ── Alerts / Health ──
        elif data == "alerts":
            data_dict = admin_health.health_dashboard()
            text = admin_health.format_health_dashboard(data_dict)
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard(
                    "health",
                    extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
                ),
            )

        # ── RAG Stats ──
        elif data == "ragstats":
            await query.edit_message_text("⏳ Fetching RAG stats...")
            result = admin_ops.rag_stats()
            if result["ok"]:
                try:
                    import json as _json

                    data = _json.loads(result["output"])
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

        # ── News ──
        elif data == "news":
            await query.edit_message_text("⏳ Fetching news feed...")
            result = admin_ops.news_feed(limit=5)
            if result["ok"]:
                try:
                    import json as _json

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

        # ── Scanner Stats ──
        elif data == "scanstats":
            await query.edit_message_text("⏳ Fetching scanner stats...")
            result = admin_ops.scan_stats()
            if result["ok"]:
                try:
                    import json as _json

                    data = _json.loads(result["output"])
                    lines = ["🔍 *Scanner Statistics*\n"]
                    metrics = data.get("metrics", {})
                    sufficiency = data.get("sufficiency", {})
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

        # ── Quick ──
        elif data == "quick":
            await query.edit_message_text(
                "⚡ *Quick Operations*",
                parse_mode=ParseMode.MARKDOWN,
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
                            InlineKeyboardButton("📤 Push All", callback_data="push_all"),
                        ],
                        [
                            InlineKeyboardButton("💻 Terminal", callback_data="term_enter"),
                            InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
                        ],
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
            await query.edit_message_text(f"⚠️ Error: {str(e)[:200]}", reply_markup=back_button("main"))
