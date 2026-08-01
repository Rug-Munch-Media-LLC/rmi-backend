"""
RMI Chain Data Backup — Health Check & Status Endpoint
=======================================================
Exposes backup pipeline status for monitoring and alerting.

Endpoints:
  GET  /api/v1/system/backup-status  — Full backup status report
  GET  /api/v1/system/backup-config  — Current backup configuration

Integration:
  - Reads last_backup.json written by chain-backup.sh
  - Checks B2 connectivity via rclone
  - Reports disk usage in backup directory
  - Returns last backup timestamp and archive info

Migrated from R2 to B2 2026-07-31

Author: RMI DevOps
Date: 2026-07-20
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger("chain_backup")

router = APIRouter(prefix="/api/v1/system", tags=["system", "backup"])

# ── Configuration paths ──────────────────────────────────────
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/path/to/backups"))
BACKUP_LOG = Path(os.getenv("BACKUP_LOG", "/var/log/rmi-chain-backup.log"))
LAST_BACKUP_FILE = BACKUP_DIR / "last_backup.json"

# B2 remote name (must match backup-config.sh)
B2_REMOTE = os.getenv("B2_REMOTE", "b2-crypt")
B2_BUCKET = os.getenv("B2_BUCKET", "rmi-backups")
B2_PATH_PREFIX = os.getenv("B2_PATH_PREFIX", "chain-data")


def _read_last_backup() -> dict[str, Any] | None:
    """Read the last_backup.json marker written by chain-backup.sh."""
    try:
        if LAST_BACKUP_FILE.exists():
            data = json.loads(LAST_BACKUP_FILE.read_text())
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read last_backup.json: %s", exc)
    return None


def _get_backup_directory_stats() -> dict[str, Any]:
    """Get disk usage stats for the backup directory."""
    stats: dict[str, Any] = {
        "path": str(BACKUP_DIR),
        "exists": BACKUP_DIR.exists(),
    }
    if not stats["exists"]:
        return stats

    try:
        # Count files by type
        archives = list(BACKUP_DIR.glob("*.tar.gz"))
        dumps = list(BACKUP_DIR.glob("*.dump"))
        natives = list(BACKUP_DIR.glob("*.native"))
        rdbs = list(BACKUP_DIR.glob("*.rdb"))

        stats["file_counts"] = {
            "archives": len(archives),
            "dumps": len(dumps),
            "native": len(natives),
            "rdb": len(rdbs),
            "total": len(archives) + len(dumps) + len(natives) + len(rdbs),
        }

        # Total size
        total_bytes = sum(f.stat().st_size for f in BACKUP_DIR.iterdir() if f.is_file())
        stats["total_size_bytes"] = total_bytes
        stats["total_size_human"] = _human_bytes(total_bytes)

        # Disk usage of the mount point
        statvfs = os.statvfs(str(BACKUP_DIR))
        stats["disk"] = {
            "total_bytes": statvfs.f_frsize * statvfs.f_blocks,
            "free_bytes": statvfs.f_frsize * statvfs.f_bfree,
            "used_pct": round((1 - statvfs.f_bfree / max(statvfs.f_blocks, 1)) * 100, 1),
        }
    except OSError as exc:
        logger.warning("Failed to get backup dir stats: %s", exc)
        stats["error"] = str(exc)[:200]

    return stats


def _check_rclone_connectivity() -> dict[str, Any]:
    """Check that rclone can list the B2 backup remote."""
    try:
        start = time.monotonic()
        # S603/S607: rclone path is controlled by the system, not user input.
        # The bucket/path vars come from env or hardcoded defaults.
        result = subprocess.run(
            ["rclone", "ls", f"{B2_REMOTE}:{B2_BUCKET}/{B2_PATH_PREFIX}/"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        if result.returncode == 0:
            obj_lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
            return {
                "reachable": True,
                "latency_ms": round(elapsed_ms, 1),
                "object_count": len(obj_lines),
                "remote": f"{B2_REMOTE}:{B2_BUCKET}/{B2_PATH_PREFIX}",
            }
        else:
            return {
                "reachable": False,
                "latency_ms": round(elapsed_ms, 1),
                "error": result.stderr.strip()[:200],
            }
    except FileNotFoundError:
        return {"reachable": False, "error": "rclone not installed"}
    except subprocess.TimeoutExpired:
        return {"reachable": False, "error": "rclone ls timed out (>15s)"}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)[:200]}


def _check_last_backup_log() -> dict[str, Any] | None:
    """Parse the last few lines of the backup log for recent status."""
    try:
        if BACKUP_LOG.exists():
            log_lines = BACKUP_LOG.read_text().strip().split("\n")
            # Get last 10 lines
            recent = log_lines[-10:] if len(log_lines) > 10 else log_lines
            # Find lines with "Backup Complete" or "ERROR"
            completion = [line for line in recent if "Backup Complete" in line]
            errors = [line for line in recent if "ERROR" in line or "FAILED" in line]
            return {
                "log_path": str(BACKUP_LOG),
                "last_completion": completion[-1] if completion else None,
                "recent_errors": errors[-5:] if errors else [],
                "total_lines": len(log_lines),
            }
    except OSError as exc:
        logger.warning("Failed to read backup log: %s", exc)
    return None


def _get_recent_archives(limit: int = 5) -> list[dict[str, Any]]:
    """List the most recent backup archives in the backup dir."""
    try:
        archives = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            {
                "name": a.name,
                "size_bytes": a.stat().st_size,
                "size_human": _human_bytes(a.stat().st_size),
                "modified": datetime.fromtimestamp(a.stat().st_mtime, tz=UTC).isoformat(),
            }
            for a in archives[:limit]
        ]
    except OSError:
        return []


def _human_bytes(b: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


# ── Endpoints ────────────────────────────────────────────────


@router.get("/backup-status")
async def backup_status() -> JSONResponse:
    """Full backup pipeline status.

    Returns:
      - last_backup: timestamp and archive info from last run
      - b2: B2 connectivity check
      - directory: backup directory disk usage and file counts
      - recent_archives: last 5 local backup archives
      - log: recent log entries (completion/errors)
      - overall: healthy/degraded/unhealthy verdict
    """
    last = _read_last_backup()
    b2 = _check_rclone_connectivity()
    dir_stats = _get_backup_directory_stats()
    archives = _get_recent_archives()
    log_status = _check_last_backup_log()

    # Determine overall health
    issues: list[str] = []
    if last is None:
        issues.append("no_backup_marker")
    elif last.get("status") != "success":
        issues.append("last_backup_failed")

    if not b2.get("reachable"):
        issues.append("b2_unreachable")

    if dir_stats.get("exists") and dir_stats.get("disk"):
        disk = dir_stats["disk"]
        if disk.get("used_pct", 0) > 90:
            issues.append("disk_usage_critical")
        elif disk.get("used_pct", 0) > 80:
            issues.append("disk_usage_high")

    if log_status and log_status.get("recent_errors"):
        issues.append("recent_errors_in_log")

    overall = ("degraded" if len(issues) <= 2 else "unhealthy") if issues else "healthy"

    return JSONResponse(
        content={
            "status": overall,
            "issues": issues,
            "last_backup": last,
            "b2": b2,
            "directory": dir_stats,
            "recent_archives": archives,
            "log": log_status,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )


@router.get("/backup-config")
async def backup_config() -> JSONResponse:
    """Current backup configuration values.

    Returns sanitized config — no secrets or infra details.
    """
    return JSONResponse(
        content={
            "backup_dir": str(BACKUP_DIR),
            "retention_days": {
                "local_backup": int(os.getenv("BACKUP_RETENTION_DAYS", "7")),
                "clickhouse": int(os.getenv("CLICKHOUSE_RETENTION_DAYS", "30")),
                "postgres": int(os.getenv("POSTGRES_RETENTION_DAYS", "90")),
            },
        }
    )
