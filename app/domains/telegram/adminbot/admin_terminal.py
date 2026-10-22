"""
admin_terminal — terminal mode sessions + command gating for the RMI Admin Bot.

Commands are classified by ``terminal_policy`` before they run: read-only runs
freely, mutating needs a per-invocation confirmation, destructive needs the typed
token, anything unclassified is refused. Execution and the JSONL audit trail
live in ``admin_terminal_exec``; presentation in ``terminal.py``; the quick menu
in ``admin_terminal_quick``.
"""

from __future__ import annotations

import hmac
import os
import secrets
import shlex
import time
from typing import Any

from app.domains.telegram.adminbot import terminal_policy as policy
from app.domains.telegram.adminbot.admin_terminal_exec import MAX_OUTPUT, deny, run_argv
from app.domains.telegram.adminbot.admin_terminal_quick import quick_commands_keyboard, run_quick
from app.domains.telegram.adminbot.config import OWNER_IDS

TERMINAL_TIMEOUT: int = 30 * 60
TERMINAL_MAX_LIFETIME: int = 60 * 60  # hard cap; activity does not extend it
CONFIRM_TTL: int = 120

#: Kept for callers that render help text (chat/owner_commands.py).
_ALLOWED_COMMANDS: frozenset[str] = policy.allowed_binaries()

_sessions: dict[int, dict[str, Any]] = {}

__all__ = [
    "MAX_OUTPUT",
    "TERMINAL_MAX_LIFETIME",
    "TERMINAL_TIMEOUT",
    "cancel",
    "confirm",
    "enter_terminal",
    "execute",
    "exit_terminal",
    "in_terminal",
    "quick_commands_keyboard",
    "run_quick",
    "touch",
]


def enter_terminal(user_id: int) -> None:
    now = time.time()
    _sessions[user_id] = {
        "cwd": policy.DEFAULT_CWD,
        "history": [],
        "entered": now,
        "started": now,
        "owner": user_id,
    }


def touch(user_id: int) -> None:
    if user_id in _sessions:
        _sessions[user_id]["entered"] = time.time()


def in_terminal(user_id: int) -> bool:
    """True only for a live session owned by a current owner (hard cap enforced)."""
    session = _sessions.get(user_id)
    if session is None:
        return False
    now = time.time()
    stale = (
        now - session.get("entered", 0) > TERMINAL_TIMEOUT or now - session.get("started", 0) > TERMINAL_MAX_LIFETIME
    )
    if stale or session.get("owner") != user_id or user_id not in OWNER_IDS:
        _sessions.pop(user_id, None)
        return False
    return True


def exit_terminal(user_id: int) -> None:
    _sessions.pop(user_id, None)


def _cd(session: dict[str, Any], argv: list[str], user_id: int) -> dict[str, Any]:
    cwd = session.get("cwd", policy.DEFAULT_CWD)
    target = argv[1] if len(argv) > 1 else policy.DEFAULT_CWD
    real = policy.resolve_in_jail(target, cwd)
    if real is None:
        return deny(user_id, cwd, argv, f"path outside the jail ({', '.join(policy.ROOTS)})")
    if not os.path.isdir(real):
        return {"ok": False, "output": "", "error": f"Not a directory: {real}", "duration": 0.0}
    session["cwd"] = real
    return {"ok": True, "output": f"→ {real}", "error": "", "duration": 0.0}


def _pending(session: dict[str, Any], argv: list[str], tier: str, reason: str) -> dict[str, Any]:
    """Stage a mutating/destructive command; it runs only after confirmation."""
    request = {"nonce": secrets.token_hex(3), "argv": argv, "tier": tier, "reason": reason}
    request.update({"cwd": session.get("cwd", policy.DEFAULT_CWD), "ts": time.time()})
    session["pending"] = request
    return {"ok": False, "output": "", "error": "", "duration": 0.0, "pending": request}


def confirm(session: dict[str, Any], nonce: str, user_id: int, typed: bool = False) -> dict[str, Any]:
    """Complete a staged command. Mutating via button; destructive needs the typed code."""
    pending = session.pop("pending", None)
    if pending is None:
        return {
            "ok": False,
            "output": "",
            "error": "No command is awaiting confirmation.",
            "duration": 0.0,
        }
    if time.time() - pending["ts"] > CONFIRM_TTL:
        return {
            "ok": False,
            "output": "",
            "error": "Confirmation expired — resend the command.",
            "duration": 0.0,
        }
    if not nonce or not hmac.compare_digest(pending["nonce"], nonce):
        return {"ok": False, "output": "", "error": "Wrong confirmation code.", "duration": 0.0}
    if pending["tier"] == policy.DANGER and not typed:
        return {
            "ok": False,
            "output": "",
            "error": "Destructive command needs the code typed back.",
            "duration": 0.0,
        }
    return run_argv(pending["argv"], pending["cwd"], user_id, pending["tier"])


def cancel(session: dict[str, Any]) -> None:
    session.pop("pending", None)


def execute(session: dict[str, Any], cmd: str, user_id: int = 0) -> dict[str, Any]:
    """Classify then run (or stage) one terminal command."""
    stripped = cmd.strip()
    if stripped == "exit":
        return {"exit": True, "output": "", "error": ""}
    session["entered"] = time.time()
    try:
        argv = shlex.split(stripped)
    except ValueError as exc:
        return {"ok": False, "output": "", "error": str(exc), "duration": 0.0}
    if not argv:
        return {"ok": False, "output": "", "error": "Empty command", "duration": 0.0}
    cwd = session.get("cwd", policy.DEFAULT_CWD)
    if argv[0] == "cd":
        return _cd(session, argv, user_id)
    tier, reason = policy.classify(argv, cwd)
    if tier == policy.DENY:
        return deny(user_id, cwd, argv, reason)
    if tier in (policy.MUTATE, policy.DANGER):
        return _pending(session, argv, tier, reason)
    session.setdefault("history", []).append(stripped)
    return run_argv(argv, cwd, user_id, tier)
