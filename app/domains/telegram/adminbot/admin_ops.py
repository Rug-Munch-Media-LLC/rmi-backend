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

import os
from typing import Any

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
    format_disk as _format_disk_impl,
    format_health,
    format_memory,
    format_status,
    health_check,
    memory_report,
    service_control,
    service_list,
    system_status,
)

# ═══════════════════════════════════════════════
# REPLY BUDGET (one Telegram message = 4096 chars)
# ═══════════════════════════════════════════════

TELEGRAM_SAFE_LIMIT = 3900
_BACKUP_HOT_ROOTS = ("/backups/qdrant", "/backups/rag", "/backups/clickhouse")


def clip_text(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> str:
    """Clip a reply to one Telegram message, keeping Markdown fences balanced.

    Truncates on a line boundary and reports ``+N more lines`` instead of
    letting ``Message is too long`` kill the reply.
    """
    if len(text) <= limit:
        return text
    body = text[: limit - 40]
    cut = body.rsplit("\n", 1)[0] if "\n" in body else body
    dropped = max(1, text.count("\n") - cut.count("\n"))
    if cut.count("```") % 2:  # unterminated code fence - close before the tail
        cut = f"{cut}\n```"
    out = f"{cut}\n… +{dropped} more lines"
    return out[:limit] if len(out) > limit else out


def _human_bytes(n: int) -> str:
    """Render bytes as whole GB (``du -B1`` reports bytes)."""
    return f"{n / 1e9:.0f}G"


def _backup_staging_section() -> str:
    """Explain the nightly ~58G spike: staging copies, not steady-state usage.

    Measured live, never hardcoded: total /backups plus the hot snapshot roots.
    ``qdrant/`` and ``rag/`` each hold one dated snapshot dir between the
    overnight snapshot run (~00:30) and the B2 offload that verifies then
    deletes it (``qdrant-b2-offload`` 04:15, ``b2-offload`` 05:45).
    """
    if not os.path.isdir("/backups"):
        return ""
    sizes: dict[str, int] = {}
    for path in ("/backups", *_BACKUP_HOT_ROOTS):
        line = _run_subprocess(["du", "-x", "-s", "-B1", path], timeout=15).get("output", "").strip()
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].isdigit():
            sizes[parts[1]] = int(parts[0])
    if not sizes:
        return ""
    hot = " · ".join(
        f"`{os.path.basename(path)}` {_human_bytes(sizes[path])}"
        for path in _BACKUP_HOT_ROOTS
        if sizes.get(path)
    )
    total = _human_bytes(sizes.get("/backups", 0))
    lines = [
        "\n🗂 *Backup staging* (the nightly spike)",
        f"/backups total: *{total}*" + (f" · {hot}" if hot else ""),
        "Snapshots land here ~00:30; `qdrant-b2-offload` (04:15) verifies +",
        "burns them, `b2-offload` (05:45) clears the rest — the 58G is a",
        "transient copy, not steady-state usage.",
        "A dated snapshot still present after 06:00 = stranded (reaper >6h).",
    ]
    return "\n".join(lines)


def format_disk(result: dict[str, Any]) -> str:
    """df output plus the backup-spike context that explains transient 58G."""
    return clip_text(_format_disk_impl(result) + _backup_staging_section())


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
