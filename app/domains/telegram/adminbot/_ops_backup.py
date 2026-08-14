"""
_ops_backup — backup sentinels, trigger, status, verify, and report.

Extracted from ``admin_ops`` (degodding 2026-08-14).
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.domains.telegram.adminbot._ops_core import _err, _ok, _run_subprocess

_BACKUP_SENTINEL = "/backups/.last-successful-backup"
_GIT_BACKUP_SENTINEL = "/backups/.last-successful-backup-all"

# Per-type backup output directories (scanned for newest artifact mtime).
_BACKUP_TYPE_DIRS: dict[str, str] = {
    "postgres": "/backups/postgres",
    "clickhouse": "/backups/clickhouse",
    "qdrant": "/backups/qdrant",
    "rag": "/backups/rag",
    "app-postgres": "/backups/app-postgres",
    "chain-indexer": "/backups/chain-indexer",
    "git": "/backups/git",
}

# Per-type backup trigger scripts (existing fleet scripts, not fabricated).
_BACKUP_SCRIPTS: dict[str, str] = {
    "full": "/root/scripts/db-backup-all.sh",
    "db": "/root/scripts/db-backup-all.sh",
    "postgres": "/root/scripts/backup-app-postgres.sh",
    "clickhouse": "/root/scripts/db-backup-all.sh",
    "qdrant": "/root/scripts/backup-qdrant.sh",
    "rag": "/root/scripts/backup-rag.sh",
    "chain-indexer": "/root/scripts/backup-chain-indexer.sh",
    "git": "/root/scripts/backup-git.sh",
    "verify": "/root/scripts/backup-verify-all.sh",
}

_B2_VERIFY_SCRIPT = "/root/scripts/backup-verify-all.sh"


def trigger_backup(btype: str = "full") -> dict[str, Any]:
    """Trigger a backup script."""
    script = _BACKUP_SCRIPTS.get(btype)
    if not script or not os.path.exists(script):
        return _err(f"Backup type '{btype}' unknown or script not found: {script}")
    return _run_subprocess(["bash", script], timeout=300)


def _newest_mtime(path: str) -> float | None:
    """Newest mtime of any regular file under a backup dir (or None)."""
    if not os.path.isdir(path):
        return None
    newest: float | None = None
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                if name.startswith("."):
                    continue
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                newest = mtime if newest is None else max(newest, mtime)
    except OSError:
        return newest
    return newest


def backup_status() -> dict[str, Any]:
    """Last successful backup per type: newest artifact mtime + age emoji.

    Also includes the overall sentinel age. Pure data + formatting helpers
    are separated for testability.
    """
    t0 = time.time()
    now = time.time()
    types: list[dict[str, Any]] = []
    for name, path in _BACKUP_TYPE_DIRS.items():
        mtime = _newest_mtime(path)
        if mtime is None:
            types.append({"name": name, "path": path, "ago_h": None, "ts": None, "level": "🔴"})
            continue
        ago_h = (now - mtime) / 3600.0
        level = "🟢" if ago_h < 36 else "🟡" if ago_h < 7 * 24 else "🔴"
        ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(mtime))
        types.append({"name": name, "path": path, "ago_h": ago_h, "ts": ts, "level": level})

    overall = _sentinel_age_hours(_BACKUP_SENTINEL)
    overall_level = (
        "🟢" if overall is not None and overall < 36 else "🟡" if overall is not None and overall < 7 * 24 else "🔴"
    )
    return {
        "ok": True,
        "types": types,
        "overall_hours": overall,
        "overall_level": overall_level,
        "duration": time.time() - t0,
    }


def _sentinel_age_hours(path: str) -> float | None:
    """Age in hours of a unix-ts sentinel file, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            ts = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return (time.time() - ts) / 3600.0


def format_backup_status(result: dict[str, Any]) -> str:
    """Render the per-type backup status card."""
    lines = ["📦 *Backup Status*\n"]
    overall = result.get("overall_hours")
    if overall is not None:
        lines.append(f"{result.get('overall_level', '🟡')} Overall: last success {overall:.1f}h ago")
    else:
        lines.append("🔴 Overall: no success sentinel")
    lines.append("")
    lines.append("*Per type (newest artifact):*")
    for t in result.get("types", []):
        if t.get("ts"):
            lines.append(f"  {t.get('level', '🟡')} `{t['name']}` — {t['ts']} ({t['ago_h']:.1f}h ago)")
        else:
            lines.append(f"  🔴 `{t['name']}` — no data")
    lines.append("\n💡 Run `/backup_run <type>` to trigger (postgres|clickhouse|qdrant|rag|chain-indexer|git|full).")
    return "\n".join(lines)


def backup_verify() -> dict[str, Any]:
    """Check B2 sync status: remote reachability + recent sync evidence.

    Runs the fleet ``backup-verify-all.sh`` if present, otherwise falls
    back to a lightweight ``rclone lsd b2-crypt:`` reachability probe and
    the b2-sync log freshness.
    """
    t0 = time.time()
    if os.path.exists(_B2_VERIFY_SCRIPT):
        r = _run_subprocess(["bash", _B2_VERIFY_SCRIPT, "--quiet"], timeout=120)
        return {**r, "duration": time.time() - t0}

    # Fallback: rclone reachability + sync log freshness.
    probe = _run_subprocess(["rclone", "lsd", "b2-crypt:"], timeout=30)
    lines = []
    if probe["ok"]:
        lines.append("✅ B2 remote `b2-crypt:` reachable")
    else:
        lines.append(f"❌ B2 remote `b2-crypt:` unreachable: {probe.get('error', '')[:200]}")
    sync_log = "/backups/b2-sync.log"
    if os.path.exists(sync_log):
        try:
            mtime = os.path.getmtime(sync_log)
            ago_h = (time.time() - mtime) / 3600.0
            lines.append(f"{'🟢' if ago_h < 48 else '🟡'} Last B2 sync log: {ago_h:.1f}h ago")
        except OSError:
            lines.append("🟡 B2 sync log unreadable")
    return _ok("\n".join(lines) if lines else "No B2 verification available", time.time() - t0)


def backup_report() -> dict[str, Any]:
    """Last successful backup timestamps + recent backup artifacts on disk."""
    t0 = time.time()

    def _sentinel_ts(path: str) -> int | None:
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    now = time.time()
    sentinels: list[tuple[str, int | None]] = [
        ("App/DB backup", _sentinel_ts(_BACKUP_SENTINEL)),
        ("Git push backup", _sentinel_ts(_GIT_BACKUP_SENTINEL)),
    ]
    lines = ["📦 *Backup Report*\n"]
    for label, ts in sentinels:
        if ts:
            ago_h = (now - ts) / 3600.0
            emoji = "🟢" if ago_h < 36 else "🟡" if ago_h < 7 * 24 else "🔴"
            lines.append(
                f"{emoji} {label}: `{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))}` ({ago_h:.1f}h ago)"
            )
        else:
            lines.append(f"🔴 {label}: no sentinel found")

    # Recent backup artifacts (sorted newest first).
    backup_dir = "/backups"
    recent: list[tuple[float, str]] = []
    if os.path.isdir(backup_dir):
        try:
            for name in os.listdir(backup_dir):
                full = os.path.join(backup_dir, name)
                if not os.path.isfile(full) or name.startswith("."):
                    continue
                try:
                    recent.append((os.path.getmtime(full), name))
                except OSError:
                    continue
        except OSError:
            pass
    recent.sort(reverse=True)
    if recent:
        lines.append("\n*Recent backup artifacts:*")
        for mtime, name in recent[:8]:
            lines.append(f"  • `{name[:48]}` {time.strftime('%m-%d %H:%M', time.gmtime(mtime))}")
    else:
        lines.append("\nNo recent backup artifacts found in /backups.")

    lines.append("\n💡 Run `/backup run` to trigger a full backup.")
    return _ok("\n".join(lines), time.time() - t0)
