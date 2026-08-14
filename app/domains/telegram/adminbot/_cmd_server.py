"""
_cmd_server — SERVICES + v4 SERVER MANAGEMENT commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops, admin_server
from app.domains.telegram.adminbot.helpers import (
    back_button,
    confirm_keyboard,
    double_confirm_keyboard,
    owner_only,
    refresh_keyboard,
    service_action_keyboard,
    svc_confirm_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# SERVICES
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_service(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = ctx.args or []
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


@owner_only
async def cmd_svc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Manage a systemd service: /svc [start|stop|restart] <service>.

    stop/restart require confirmation (destructive); start runs directly.
    """
    args = ctx.args or []
    if len(args) < 2 or args[0].lower() not in ("start", "stop", "restart"):
        await update.message.reply_text(
            "🛠 *Service Control*\n\n"
            "Usage: `/svc <action> <service>`\n\n"
            "Actions: `start`, `stop`, `restart`\n\n"
            "Examples:\n"
            "`/svc restart rmi-backend`\n"
            "`/svc start rmiadminbot`\n"
            "`/svc stop rugmunchbot`\n\n"
            "⚠️ `stop` and `restart` require confirmation.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    action = args[0].lower()
    service = args[1]
    if not all(c.isalnum() or c in "-_" for c in service):
        await update.message.reply_text(f"❌ Invalid service name: `{service}`", parse_mode=ParseMode.MARKDOWN)
        return

    if action in ("stop", "restart"):
        await update.message.reply_text(
            f"⚠️ *Confirm {action}*\n\n{action} systemd service `{service}`?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=svc_confirm_keyboard(action, service),
        )
        return

    msg = await update.message.reply_text(f"⏳ Starting `{service}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_server.service_control(service, action)
    if result["ok"]:
        if admin_audit is not None:
            admin_audit.log_action("service.start", service, update.effective_user.id if update.effective_user else 0)
        await msg.edit_text(
            f"✅ `{action} {service}` — done\n\n```\n{result['output'][:1500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}", parse_mode=ParseMode.MARKDOWN, reply_markup=back_button("services")
        )


@owner_only
async def cmd_svc_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """List systemd services with status — /svc_list [filter]."""
    filter_str = ctx.args[0] if ctx.args else ""
    msg = await update.message.reply_text("⏳ Listing services...")
    result = admin_server.services_report(filter_str=filter_str)
    await msg.edit_text(
        admin_server.format_services(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("svc_list", extra=[("🛠 All", "services")] if filter_str else None),
    )


@owner_only
async def cmd_svc_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Tail journalctl for a systemd service: /svc_logs <service> [lines]."""
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "📜 *Service Logs*\n\n"
            "Usage: `/svc_logs <service> [lines]`\n\n"
            "Examples:\n"
            "`/svc_logs rmiadminbot`\n"
            "`/svc_logs rugmunchbot 100`\n\n"
            "Default: 50 lines. Max: 500.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    service = args[0]
    lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for `{service}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_server.service_logs(service, lines)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📜 `{service}.service` (last {lines} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )
    else:
        await msg.edit_text(
            f"❌ {result.get('error', 'no output')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )


# ═══════════════════════════════════════════════
# NEW v4 — SERVER MANAGEMENT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
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
    if update.message is None:
        return
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
    if update.message is None:
        return
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
    if update.message is None:
        return
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
    if update.message is None:
        return
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
    if update.message is None:
        return
    """Restart a systemd service — requires confirmation."""
    args = ctx.args or []
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
# v5 — CONTROL PANEL / BOT MANAGEMENT / REBOOT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_admin_reboot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Reboot the server — requires DOUBLE confirmation — OWNER ONLY."""
    args = ctx.args or []
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
