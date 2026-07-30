"""
admin_terminal — terminal mode for the RMI Admin Bot.

Manages terminal sessions: enter, execute, format, and exit.
Commands are allowlist-gated for safety.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Commands allowed in terminal mode (base command only)
_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "ls", "pwd", "cd", "df", "free", "ps", "docker", "systemctl",
        "git", "journalctl", "cat", "head", "tail", "grep", "uptime",
        "who", "w", "last", "netstat", "ss", "ip", "ping", "curl",
        "docker-compose", "du", "top", "htop", "lsof", "stat",
        "find", "tree", "mkdir", "touch", "cp", "mv",
    }
)

_sessions: dict[int, dict[str, Any]] = {}

def enter_terminal(user_id: int) -> None:
    _sessions[user_id] = {"cwd": "/root", "history": []}

def in_terminal(user_id: int) -> bool:
    return user_id in _sessions

def exit_terminal(user_id: int) -> None:
    _sessions.pop(user_id, None)

def _get_base_command(cmd: str) -> str | None:
    """Extract the base command name. Returns None on parse error."""
    try:
        return shlex.split(cmd.strip())[0]
    except (ValueError, IndexError):
        return None

def _check_safe_cmd(cmd: str) -> str | None:
    """Check if command is allowed. Returns None if ok, error string if blocked."""
    stripped = cmd.strip()
    if not stripped:
        return "Empty command"
    base = _get_base_command(stripped)
    if base is None:
        return "Invalid command syntax"
    if stripped == "exit":
        return None  # handled by execute()
    if base not in _ALLOWED_COMMANDS:
        return f"Command not allowed: '{base}'. Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}"
    return None

def execute(session: dict[str, Any], cmd: str) -> dict[str, Any]:
    stripped = cmd.strip()
    if stripped == "exit":
        return {"exit": True, "output": "", "error": ""}

    # Check allowlist
    err_msg = _check_safe_cmd(stripped)
    if err_msg:
        return {"ok": False, "output": "", "error": err_msg, "duration": 0.0}

    try:
        parts = shlex.split(stripped)
    except ValueError as exc:
        return {"ok": False, "output": "", "error": str(exc), "duration": 0.0}

    if not parts:
        return {"ok": False, "output": "", "error": "Empty command", "duration": 0.0}

    # Handle cd internally (subprocess can't persist directory changes)
    if parts[0] == "cd":
        if len(parts) >= 2:
            session["cwd"] = parts[1]
        return {"ok": True, "output": "", "error": "", "duration": 0.0}

    t0 = time.time()
    try:
        r = subprocess.run(
            parts,
            shell=False,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=session.get("cwd", "/root"),
        )
        session["history"].append(stripped)
        return {
            "ok": r.returncode == 0,
            "output": r.stdout or r.stderr,
            "error": r.stderr if r.returncode != 0 else "",
            "duration": time.time() - t0,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": "Command timed out (15s)",
            "duration": time.time() - t0,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "output": "",
            "error": f"Command not found: {parts[0]}",
            "duration": time.time() - t0,
        }
    except (ConnectionError, OSError, TimeoutError) as e:
        return {"ok": False, "output": "", "error": str(e), "duration": time.time() - t0}

def format_prompt(session: dict[str, Any]) -> str:
    return f"`{session.get('cwd', '/root')}$`"

def format_result(result: dict[str, Any], session: dict[str, Any]) -> str:
    output = result.get("output", "") or result.get("error", "")
    return f"```\n{output[:2000]}\n```"

def run_quick(cmd_name: str) -> dict[str, Any]:
    """Run a predefined quick command — no shell, allowlist-gated."""
    quick: dict[str, list[str]] = {
        "df": ["df", "-h", "--total"],
        "mem": ["free", "-h"],
        "ps": ["ps", "aux", "--sort=-%mem"],
        "docker": ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
        "uptime": ["uptime"],
        "disk": ["df", "-h", "/"],
        "load": ["cat", "/proc/loadavg"],
        "docker-all": ["docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
    }
    cmd_parts = quick.get(cmd_name)
    if cmd_parts is None:
        return {"ok": False, "output": "", "error": f"Unknown quick command: '{cmd_name}'"}

    t0 = time.time()
    try:
        r = subprocess.run(
            cmd_parts,
            shell=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "ok": r.returncode == 0,
            "output": r.stdout or r.stderr,
            "error": r.stderr if r.returncode != 0 else "",
            "duration": time.time() - t0,
        }
    except (ValueError, TypeError, KeyError, OSError) as e:
        return {"ok": False, "output": "", "error": str(e), "duration": time.time() - t0}

def quick_commands_keyboard() -> Any:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Disk All", callback_data="term_quick_df"),
                InlineKeyboardButton("🧠 Memory", callback_data="term_quick_mem"),
            ],
            [
                InlineKeyboardButton("📋 Processes", callback_data="term_quick_ps"),
                InlineKeyboardButton("🐳 Docker", callback_data="term_quick_docker"),
            ],
            [
                InlineKeyboardButton("⏱ Uptime", callback_data="term_quick_uptime"),
                InlineKeyboardButton("📊 Load", callback_data="term_quick_load"),
            ],
            [
                InlineKeyboardButton("💾 Disk /", callback_data="term_quick_disk"),
                InlineKeyboardButton("🐳 All Containers", callback_data="term_quick_docker-all"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="main")],
        ]
    )
