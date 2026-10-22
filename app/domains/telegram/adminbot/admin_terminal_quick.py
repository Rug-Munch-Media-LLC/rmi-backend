"""
admin_terminal_quick — fixed quick-command menu for terminal mode.

Every entry is a hard-coded argv list (never user input), pre-classified as
read-only by ``terminal_policy`` and audited like any other terminal command.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.adminbot import admin_terminal_exec, terminal_policy as policy

QUICK: dict[str, list[str]] = {
    "df": ["df", "-h", "--total"],
    "mem": ["free", "-h"],
    "ps": ["ps", "aux", "--sort=-%mem"],
    "docker": ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
    "uptime": ["uptime"],
    "disk": ["df", "-h", "/"],
    "load": ["cat", "/proc/loadavg"],
    "docker-all": ["docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
}


def run_quick(cmd_name: str, user_id: int = 0) -> dict[str, Any]:
    """Run one predefined command; unknown names and non-read-only entries are refused."""
    cmd_parts = QUICK.get(cmd_name)
    if cmd_parts is None:
        return {
            "ok": False,
            "output": "",
            "error": f"Unknown quick command: '{cmd_name}'",
            "duration": 0.0,
        }
    tier, _ = policy.classify(cmd_parts, policy.DEFAULT_CWD)
    if tier != policy.READ:
        return {
            "ok": False,
            "output": "",
            "error": "Quick command is not read-only",
            "duration": 0.0,
        }
    return admin_terminal_exec.run_argv(cmd_parts, policy.DEFAULT_CWD, user_id, tier)


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
