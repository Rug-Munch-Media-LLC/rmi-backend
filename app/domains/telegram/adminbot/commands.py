"""
Admin Bot - Command Handlers (re-export shim)
============================================
All /command handler functions for the RMI Admin Bot.

Split 2026-08-14 into focused submodules:
  - _cmd_core:   START/HELP/STATUS/DISK/MEM/SHELL/LOGS/HEALTH/QUICK
  - _cmd_server: SERVICES + v4 SERVER MANAGEMENT
  - _cmd_docker: DOCKER + DEPLOY
  - _cmd_backup: BACKUP
  - _cmd_git:    GIT + PUSH
  - _cmd_ops:    OC/CHAIN/ALERT/RAG/NEWS/SCANNER/INDEXER + BOT MANAGEMENT

This module re-exports the full ``cmd_*`` surface so existing importers
(``bot.py``, tests) keep working unchanged.
"""

from __future__ import annotations

from app.domains.telegram.adminbot._cmd_backup import (
    cmd_admin_backups,
    cmd_backup,
    cmd_backup_run,
    cmd_backup_status,
    cmd_backup_verify,
)
from app.domains.telegram.adminbot._cmd_core import (
    cmd_alerts,
    cmd_disk,
    cmd_health,
    cmd_help,
    cmd_logs,
    cmd_mem,
    cmd_menu,
    cmd_quick,
    cmd_shell,
    cmd_start,
    cmd_status,
)
from app.domains.telegram.adminbot._cmd_docker import (
    cmd_deploy,
    cmd_dlogs,
    cmd_docker,
    cmd_dprune,
    cmd_dps,
    cmd_drestart,
    cmd_dstats,
)
from app.domains.telegram.adminbot._cmd_git import (
    cmd_git,
    cmd_push,
)
from app.domains.telegram.adminbot._cmd_ops import (
    cmd_admin_bot_broadcast,
    cmd_admin_bot_broadcasts,
    cmd_admin_bot_stats,
    cmd_admin_bot_status,
    cmd_admin_bot_toggle,
    cmd_admin_bot_users,
    cmd_admin_logs,
    cmd_admin_scanner,
    cmd_alert,
    cmd_chain,
    cmd_indexer,
    cmd_news,
    cmd_oc,
    cmd_ragstats,
    cmd_scanstats,
)
from app.domains.telegram.adminbot._cmd_server import (
    cmd_admin_reboot,
    cmd_certs,
    cmd_net,
    cmd_repair,
    cmd_restart,
    cmd_service,
    cmd_services,
    cmd_svc,
    cmd_svc_list,
    cmd_svc_logs,
    cmd_top,
    cmd_uptime,
)

__all__ = [
    "cmd_admin_backups",
    "cmd_admin_bot_broadcast",
    "cmd_admin_bot_broadcasts",
    "cmd_admin_bot_stats",
    "cmd_admin_bot_status",
    "cmd_admin_bot_toggle",
    "cmd_admin_bot_users",
    "cmd_admin_logs",
    "cmd_admin_reboot",
    "cmd_admin_scanner",
    "cmd_alert",
    "cmd_alerts",
    "cmd_backup",
    "cmd_backup_run",
    "cmd_backup_status",
    "cmd_backup_verify",
    "cmd_certs",
    "cmd_chain",
    "cmd_deploy",
    "cmd_disk",
    "cmd_dlogs",
    "cmd_docker",
    "cmd_dprune",
    "cmd_dps",
    "cmd_drestart",
    "cmd_dstats",
    "cmd_git",
    "cmd_health",
    "cmd_help",
    "cmd_indexer",
    "cmd_logs",
    "cmd_mem",
    "cmd_menu",
    "cmd_net",
    "cmd_news",
    "cmd_oc",
    "cmd_push",
    "cmd_quick",
    "cmd_ragstats",
    "cmd_repair",
    "cmd_restart",
    "cmd_scanstats",
    "cmd_service",
    "cmd_services",
    "cmd_shell",
    "cmd_start",
    "cmd_status",
    "cmd_svc",
    "cmd_svc_list",
    "cmd_svc_logs",
    "cmd_top",
    "cmd_uptime",
]
