"""
Admin Bot - Callback handlers: docker & compose
=================================================
Docker PS/stats/prune, docker management menus, compose, and container
restart confirmation flows. Dispatched by ``callbacks._dispatch.handle_callback``
in original chain order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_docker, admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_confirm_keyboard,
    docker_menu,
    refresh_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle docker/compose callback data. Returns True when matched."""
    # ── v4: Docker PS ──
    if data == "dps":
        await query.edit_message_text("⏳ Listing containers...")
        result = admin_docker.docker_ps_table()
        await query.edit_message_text(
            admin_docker.format_docker_ps(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("dps", extra=[("📊 Stats", "dstats")]),
        )
        return True

    # ── v4: Docker stats ──
    elif data == "dstats":
        await query.edit_message_text("⏳ Sampling container stats...")
        result = admin_docker.docker_stats_top(limit=10)
        await query.edit_message_text(
            admin_docker.format_docker_stats(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("dstats", extra=[("📋 PS", "dps")]),
        )
        return True

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
        return True
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
        return True
    elif data == "dprune_run":
        await query.edit_message_text("🧹 Pruning docker...")
        if admin_audit is not None:
            admin_audit.log_action("docker.prune", "system prune -af --volumes", query.from_user.id)
        result = admin_docker.docker_prune()
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Prune complete\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("dprune"),
        )
        return True
    elif data.startswith("dprune_cancel"):
        await query.edit_message_text("❌ Prune cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── Docker ──
    elif data == "docker":
        await query.edit_message_text(
            "🐳 *Docker Management*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=docker_menu(),
        )
        return True

    elif data == "docker_ps":
        out = admin_ops.docker_ps()
        await query.edit_message_text(
            f"🐳 *Docker PS*\n\n```\n{out[:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("docker"),
        )
        return True

    elif data == "docker_stats":
        result = admin_ops.docker_stats()
        await query.edit_message_text(
            f"📊 *Container Stats*\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("docker"),
        )
        return True

    elif data.startswith("docker_restart_"):
        container = data.replace("docker_restart_", "")
        if admin_audit is not None:
            admin_audit.log_action("docker.restart", container, query.from_user.id)
        result = admin_ops.docker_restart(container)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Restarted `{container}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("docker"),
        )
        return True

    # ── Compose ──
    elif data == "compose_ps":
        result = admin_ops.docker_compose("ps")
        await query.edit_message_text(
            f"📋 *Compose PS*\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("docker"),
        )
        return True

    elif data == "compose_restart":
        await query.edit_message_text("⏳ Restarting infra...")
        if admin_audit is not None:
            admin_audit.log_action("compose.restart", "all", query.from_user.id)
        result = admin_ops.docker_compose("restart")
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Infra restarted\n\n```\n{result['output'][:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("docker"),
        )
        return True

    # ── Docker restart (confirmation flow) ──
    elif data == "drestart_menu":
        containers = [
            "rmi-backend",
            "rmi-postgres",
            "rmi-redis",
            "rmi-clickhouse",
            "rmi-qdrant",
            "degenfeed-meilisearch",
            "rmi-qdrant",
        ]
        buttons = []
        row = []
        for c in containers:
            row.append(InlineKeyboardButton(c, callback_data=f"drestart_pick_{c}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_docker")])
        await query.edit_message_text(
            "🔄 *Restart Container* — pick one:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return True
    elif data.startswith("drestart_pick_"):
        container = data.replace("drestart_pick_", "")
        await query.edit_message_text(
            f"⚠️ *Confirm restart*\n\nRestart docker container `{container}`?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=docker_confirm_keyboard(container),
        )
        return True
    elif data.startswith("drestart_confirm_"):
        container = data.replace("drestart_confirm_", "")
        if admin_audit is not None:
            admin_audit.log_action("docker.restart", container, query.from_user.id)
        result = admin_docker.docker_restart(container)
        status_emoji = "✅" if result["ok"] else "❌"
        await query.edit_message_text(
            f"{status_emoji} Restarted `{container}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("dps", extra=[("📋 PS", "dps")]),
        )
        return True
    elif data.startswith("drestart_cancel"):
        await query.edit_message_text("❌ Container restart cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    return False
