"""
admin_ops — fleet operations (re-export shim).

Split 2026-08-14 into focused submodules:
  - _ops_core:   subprocess primitives + /sh read-only shell allowlist
  - _ops_system: system status, disk, memory, systemd, health
  - _ops_docker: docker + compose deploy helpers
  - _ops_backup: backup sentinels, trigger/status/verify/report
  - _ops_git:    known repos + git status + push-all
  - _ops_intel:  chain/indexer status, alert dispatch, backend stats

This module re-exports the public surface so existing importers keep
working unchanged.
"""

from __future__ import annotations

import os  # noqa: F401  (kept for admin_ops.os attribute compat)

from app.domains.telegram.adminbot._ops_backup import (
    _BACKUP_SENTINEL,
    _GIT_BACKUP_SENTINEL,
    backup_report,
    backup_status,
    backup_verify,
    format_backup_status,
    trigger_backup,
)
from app.domains.telegram.adminbot._ops_core import (
    _err,
    _ok,
    _run_subprocess,
    _validate_name,
    run_shell,
)
from app.domains.telegram.adminbot._ops_docker import (
    deploy_service,
    docker_compose,
    docker_logs,
    docker_ps,
    docker_restart,
    docker_stats,
)
from app.domains.telegram.adminbot._ops_git import (
    RMI_REPOS,
    git_status,
    push_all_repos,
)
from app.domains.telegram.adminbot._ops_intel import (
    chain_status,
    indexer_status,
    news_feed,
    rag_stats,
    scan_stats,
    send_alert,
)
from app.domains.telegram.adminbot._ops_system import (
    disk_usage,
    format_disk,
    format_health,
    format_memory,
    format_status,
    health_check,
    memory_report,
    service_control,
    service_list,
    system_status,
)

__all__ = [
    "RMI_REPOS",
    "_BACKUP_SENTINEL",
    "_GIT_BACKUP_SENTINEL",
    "_err",
    "_ok",
    "_run_subprocess",
    "_validate_name",
    "backup_report",
    "backup_status",
    "backup_verify",
    "chain_status",
    "deploy_service",
    "disk_usage",
    "docker_compose",
    "docker_logs",
    "docker_ps",
    "docker_restart",
    "docker_stats",
    "format_backup_status",
    "format_disk",
    "format_health",
    "format_memory",
    "format_status",
    "git_status",
    "health_check",
    "indexer_status",
    "memory_report",
    "news_feed",
    "push_all_repos",
    "rag_stats",
    "run_shell",
    "scan_stats",
    "send_alert",
    "service_control",
    "service_list",
    "system_status",
    "trigger_backup",
]
