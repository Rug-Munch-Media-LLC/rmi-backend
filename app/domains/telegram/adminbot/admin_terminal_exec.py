"""
admin_terminal_exec — audited, env-scrubbed process execution for the terminal.

Split out of ``admin_terminal`` so the executor is testable on its own: tests can
call :func:`run_argv` directly (bypassing classification) to prove that a real
child process cannot see the bot's secrets, and that every spawn leaves a start
and an end record in the audit trail.

The trail is JSONL (ts, user, cwd, argv, tier, exit, duration). The primary path
is ``/var/log/rmiadminbot/term.jsonl``; ``/var/log`` is read-only under the
bot's ``ProtectSystem=strict`` sandbox, so the fallback ``/backups`` path is
used there. A command is refused when neither path is writable.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

from app.domains.telegram.adminbot import terminal_policy as policy

logger = logging.getLogger("admin_bot.terminal")

EXEC_TIMEOUT: int = 15
MAX_OUTPUT: int = 2000

AUDIT_LOG: str = os.environ.get("ADMIN_TERM_AUDIT_LOG", "/var/log/rmiadminbot/term.jsonl")
_FALLBACK_AUDIT: str = "/backups/.adminbot-term.jsonl"


def _audit(record: dict[str, Any]) -> bool:
    """Append one JSONL record. Returns False when no path is writable."""
    for path in (AUDIT_LOG, _FALLBACK_AUDIT):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
            return True
        except OSError:
            continue
    logger.error("terminal audit unwritable (%s, %s)", AUDIT_LOG, _FALLBACK_AUDIT)
    return False


def audit(record: dict[str, Any]) -> bool:
    """Public audit hook (deny records from the policy layer)."""
    return _audit(record)


def deny(user_id: int, cwd: str, argv: list[str], reason: str) -> dict[str, Any]:
    """Record a refusal and return the refusal envelope."""
    audit(
        {
            "ts": time.time(),
            "phase": "deny",
            "user": user_id,
            "cwd": cwd,
            "argv": argv,
            "reason": reason,
        }
    )
    return {"ok": False, "output": "", "error": f"⛔ Refused: {reason}", "duration": 0.0}


def run_argv(argv: list[str], cwd: str, user_id: int, tier: str) -> dict[str, Any]:
    """Execute argv (no shell) with a scrubbed env, audited before and after."""
    base = {"ts": time.time(), "user": user_id, "cwd": cwd, "argv": argv, "tier": tier}
    if not audit({**base, "phase": "start"}):
        return {
            "ok": False,
            "output": "",
            "error": "⛔ Refused: audit trail unavailable.",
            "duration": 0.0,
        }
    t0 = time.time()
    code: int | None = None
    out, err = "", ""
    try:
        r = subprocess.run(  # noqa: S603 — argv list, shell=False, policy-classified
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT,
            cwd=cwd,
            env=policy.scrubbed_env(),
        )
        code, out, err = r.returncode, r.stdout or r.stderr, r.stderr
    except subprocess.TimeoutExpired:
        err = f"Command timed out ({EXEC_TIMEOUT}s)"
    except FileNotFoundError:
        err = f"Command not found: {argv[0]}"
    except OSError as exc:
        err = str(exc)
    duration = time.time() - t0
    if not audit({**base, "phase": "end", "exit": code, "duration": round(duration, 3)}):
        err = f"{err}\n⚠️ audit end-record failed".strip()
    ok = code == 0
    return {"ok": ok, "output": out, "error": "" if ok else err, "duration": duration, "tier": tier}
