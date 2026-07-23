"""
admin_terminal — terminal mode stubs.

Replace with real session management when wired to infrastructure.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

_sessions: dict[int, dict[str, Any]] = {}


def enter_terminal(user_id: int) -> None:
    _sessions[user_id] = {"cwd": "/root", "history": []}


def in_terminal(user_id: int) -> bool:
    return user_id in _sessions


def exit_terminal(user_id: int) -> None:
    _sessions.pop(user_id, None)


def execute(session: dict[str, Any], cmd: str) -> dict[str, Any]:
    if cmd.strip() == "exit":
        return {"exit": True, "output": "", "error": ""}
    import subprocess
    import time

    t0 = time.time()
    try:
        r = subprocess.run(
            cmd,
            shell=True,  # nosec - admin shell by design
            capture_output=True,
            text=True,
            timeout=15,
            cwd=session.get("cwd", "/root"),
        )
        session["history"].append(cmd)
        return {
            "ok": r.returncode == 0,
            "output": r.stdout or r.stderr,
            "error": r.stderr if r.returncode != 0 else "",
            "duration": time.time() - t0,
        }
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e), "duration": time.time() - t0}


def format_prompt(session: dict[str, Any]) -> str:
    return f"`{session.get('cwd', '/root')}$`"


def format_result(result: dict[str, Any], session: dict[str, Any]) -> str:
    output = result.get("output", "") or result.get("error", "")
    return f"```\n{output[:2000]}\n```"


def run_quick(cmd_name: str) -> dict[str, Any]:
    import subprocess

    quick = {
        "df": "df -h",
        "mem": "free -h",
        "ps": "ps aux --sort=-%mem | head -15",
        "docker": "docker ps --format 'table {{.Names}}\t{{.Status}}'",
        "uptime": "uptime",
    }
    cmd = quick.get(cmd_name, cmd_name)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)  # nosec - admin shell by design
        return {"ok": r.returncode == 0, "output": r.stdout or r.stderr, "error": ""}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def quick_commands_keyboard() -> Any:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Disk", callback_data="term_quick_df"),
                InlineKeyboardButton("🧠 Memory", callback_data="term_quick_mem"),
            ],
            [
                InlineKeyboardButton("📋 Processes", callback_data="term_quick_ps"),
                InlineKeyboardButton("🐳 Docker", callback_data="term_quick_docker"),
            ],
            [
                InlineKeyboardButton("⏱ Uptime", callback_data="term_quick_uptime"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="main")],
        ]
    )
