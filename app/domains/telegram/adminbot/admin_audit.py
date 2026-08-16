"""
admin_audit — append-only audit trail for admin bot actions.

Every destructive/privileged action (docker restart, service control,
backup triggers, shell, reboot, prune) appends a JSONL record here so
the owner can review who did what via /admin_logs.

The log is fail-open: if the file cannot be written the command still
succeeds (we never let auditing break an operation). The default path
is writable under the rmiadminbot systemd sandbox (ReadWritePaths
includes /backups; /var/log is NOT writable with ProtectSystem=strict).

Record shape::

    {"ts": 1723000000.0, "time": "2026-08-08 14:00:00 UTC",
     "user_id": 123456789, "action": "docker.restart", "detail": "rmi-backend"}

The record is a single JSON object per line (JSONL), append-only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("admin_bot.admin_audit")

# Writable under the rmiadminbot systemd sandbox; override in tests.
AUDIT_LOG_PATH: str = os.environ.get("ADMIN_AUDIT_LOG", "/backups/.adminbot-audit.log")

_AUDIT_MAX_LINE: int = 500


def log_action(action: str, detail: str = "", user_id: int | None = None) -> None:
    """Append one audit record (fail-open). Never raises."""
    record = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "user_id": user_id,
        "action": action,
        "detail": (detail or "")[:_AUDIT_MAX_LINE],
    }
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("admin_audit: cannot write audit log %s: %s", AUDIT_LOG_PATH, exc)


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one JSONL record; returns None for blank/corrupt lines."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def recent_actions(limit: int = 20) -> dict[str, Any]:
    """Read the most recent N audit records (newest first)."""
    t0 = time.time()
    limit = max(1, min(int(limit), 100))
    if not os.path.exists(AUDIT_LOG_PATH):
        return {"ok": True, "records": [], "error": "", "duration": time.time() - t0}
    records: list[dict[str, Any]] = []
    try:
        with open(AUDIT_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                rec = _parse_line(line)
                if rec:
                    records.append(rec)
    except OSError as exc:
        return {"ok": False, "records": [], "error": f"Cannot read audit log: {exc}", "duration": time.time() - t0}
    records.reverse()
    return {"ok": True, "records": records[:limit], "error": "", "duration": time.time() - t0}


def format_admin_logs(result: dict[str, Any]) -> str:
    """Render the /admin_logs card from a recent_actions() result."""
    if not result.get("ok"):
        return f"❌ {result.get('error', 'audit log unavailable')}"
    records = result.get("records", [])
    if not records:
        return "📋 No admin actions logged yet."
    lines = ["📋 *Recent Admin Actions*\n"]
    for r in records:
        user = r.get("user_id")
        who = f"`{user}`" if user is not None else "`system`"
        action = r.get("action", "?")
        detail = (r.get("detail") or "")[:80]
        lines.append(f"• {r.get('time', '?')} · {who} · `{action}`")
        if detail:
            lines.append(f"   ↳ {detail}")
    lines.append(f"\nShowing {len(records)} most recent · `{os.path.basename(AUDIT_LOG_PATH)}`")
    return "\n".join(lines)
