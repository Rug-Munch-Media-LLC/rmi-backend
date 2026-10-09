"""
Command handler registration — extracted from bot.py for structure gate.
"""

from __future__ import annotations

from typing import Any

from telegram.ext import (
    Application,
    CommandHandler,
)

from app.domains.telegram.adminbot.ai_gateway import cmd_ai
from app.domains.telegram.adminbot.commands import (
    cmd_admin_backups,
    cmd_admin_bot_broadcast,
    cmd_admin_bot_broadcasts,
    cmd_admin_bot_stats,
    cmd_admin_bot_status,
    cmd_admin_bot_toggle,
    cmd_admin_bot_users,
    cmd_admin_logs,
    cmd_admin_reboot,
    cmd_admin_scanner,
    cmd_alert,
    cmd_alerts,
    cmd_backup,
    cmd_backup_run,
    cmd_backup_status,
    cmd_backup_verify,
    cmd_certs,
    cmd_chain,
    cmd_cleanup,
    cmd_commands,
    cmd_deploy,
    cmd_digest,
    cmd_disk,
    cmd_dlogs,
    cmd_docker,
    cmd_dprune,
    cmd_dps,
    cmd_drestart,
    cmd_dstats,
    cmd_git,
    cmd_health,
    cmd_help,
    cmd_indexer,
    cmd_logins,
    cmd_logs,
    cmd_mem,
    cmd_menu,
    cmd_net,
    cmd_news,
    cmd_oc,
    cmd_pressure,
    cmd_push,
    cmd_quick,
    cmd_ragstats,
    cmd_repair,
    cmd_restart,
    cmd_scanstats,
    cmd_service,
    cmd_services,
    cmd_shell,
    cmd_start,
    cmd_status,
    cmd_svc,
    cmd_svc_list,
    cmd_svc_logs,
    cmd_top,
    cmd_uptime,
)
from app.domains.telegram.adminbot.engine_gateway import cmd_ask, cmd_engine
from app.domains.telegram.adminbot.power_ops import cmd_community, cmd_heavy
from app.domains.telegram.adminbot.selftest import cmd_selftest
from app.domains.telegram.adminbot.terminal import cmd_terminal


def register_canonical_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register all canonical command handlers."""
    # ── Core Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("commands", cmd_commands))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("docker", cmd_docker))
    app.add_handler(CommandHandler("sh", cmd_shell))
    app.add_handler(CommandHandler("s", cmd_shell))  # alias
    app.add_handler(CommandHandler("service", cmd_service))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("deploy", cmd_deploy))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("repair", cmd_repair))
    app.add_handler(CommandHandler("alerts", cmd_alerts))  # legacy alias → health dashboard
    app.add_handler(CommandHandler("health", cmd_health))  # explicit health dashboard command
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("term", cmd_terminal))
    app.add_handler(CommandHandler("ai", cmd_ai))

    # ── NEW Commands v3 ──
    app.add_handler(CommandHandler("disk", cmd_disk))
    app.add_handler(CommandHandler("mem", cmd_mem))
    app.add_handler(CommandHandler("oc", cmd_oc))
    app.add_handler(CommandHandler("git", cmd_git))
    app.add_handler(CommandHandler("push", cmd_push))
    app.add_handler(CommandHandler("chain", cmd_chain))
    app.add_handler(CommandHandler("alert", cmd_alert))

    # ── NEW Commands v4 (Intelligence) ──
    app.add_handler(CommandHandler("ragstats", cmd_ragstats))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("scanstats", cmd_scanstats))
    app.add_handler(CommandHandler("indexer", cmd_indexer))

    # ── NEW Commands v4 (Server + Docker management) ──
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("uptime", cmd_uptime))
    app.add_handler(CommandHandler("net", cmd_net))
    app.add_handler(CommandHandler("certs", cmd_certs))
    app.add_handler(CommandHandler("services", cmd_services))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("dps", cmd_dps))
    app.add_handler(CommandHandler("dlogs", cmd_dlogs))
    app.add_handler(CommandHandler("dstats", cmd_dstats))
    app.add_handler(CommandHandler("dprune", cmd_dprune))
    app.add_handler(CommandHandler("drestart", cmd_drestart))

    # ── NEW Commands v6 (Service + Backup management) ──
    app.add_handler(CommandHandler("svc", cmd_svc))
    app.add_handler(CommandHandler("svc_list", cmd_svc_list))
    app.add_handler(CommandHandler("svc_logs", cmd_svc_logs))
    app.add_handler(CommandHandler("backup_status", cmd_backup_status))
    app.add_handler(CommandHandler("backup_run", cmd_backup_run))
    app.add_handler(CommandHandler("backup_verify", cmd_backup_verify))
    app.add_handler(CommandHandler("admin_logs", cmd_admin_logs))
    app.add_handler(CommandHandler("admin_svc", cmd_svc))
    app.add_handler(CommandHandler("admin_svc_list", cmd_svc_list))
    app.add_handler(CommandHandler("admin_svc_logs", cmd_svc_logs))
    app.add_handler(CommandHandler("admin_backup_status", cmd_backup_status))
    app.add_handler(CommandHandler("admin_backup_run", cmd_backup_run))
    app.add_handler(CommandHandler("admin_backup_verify", cmd_backup_verify))

    # ── NEW Commands v7 (Deep server control) ──
    app.add_handler(CommandHandler("pressure", cmd_pressure))
    app.add_handler(CommandHandler("logins", cmd_logins))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CommandHandler("digest", cmd_digest))

    # ── NEW Commands v5 (admin_* aliases + control panel) ──
    app.add_handler(CommandHandler("admin_services", cmd_services))
    app.add_handler(CommandHandler("admin_docker_ps", cmd_dps))
    app.add_handler(CommandHandler("admin_docker_logs", cmd_dlogs))
    app.add_handler(CommandHandler("admin_disk", cmd_disk))
    app.add_handler(CommandHandler("admin_memory", cmd_mem))
    app.add_handler(CommandHandler("admin_uptime", cmd_uptime))
    app.add_handler(CommandHandler("admin_backups", cmd_admin_backups))
    app.add_handler(CommandHandler("admin_rag", cmd_ragstats))
    app.add_handler(CommandHandler("admin_scanner", cmd_admin_scanner))
    app.add_handler(CommandHandler("admin_restart", cmd_restart))
    app.add_handler(CommandHandler("admin_reboot", cmd_admin_reboot))

    # ── NEW Commands v5 (bot management) ──
    app.add_handler(CommandHandler("admin_bot_stats", cmd_admin_bot_stats))
    app.add_handler(CommandHandler("admin_bot_status", cmd_admin_bot_status))
    app.add_handler(CommandHandler("admin_bot_toggle", cmd_admin_bot_toggle))
    app.add_handler(CommandHandler("admin_bot_broadcast", cmd_admin_bot_broadcast))
    app.add_handler(CommandHandler("admin_bot_broadcasts", cmd_admin_bot_broadcasts))

    # ── v8: engines + power ops (P4/P5) ──
    app.add_handler(CommandHandler("engine", cmd_engine))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("heavy", cmd_heavy))
    app.add_handler(CommandHandler("community", cmd_community))
    app.add_handler(CommandHandler("selftest", cmd_selftest))
    app.add_handler(CommandHandler("admin_bot_users", cmd_admin_bot_users))
