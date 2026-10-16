"""Telegram-native menu for the admin bot.

The shell `menu` is right for a keyboard, but on Telegram the equivalent is an
inline keyboard: tappable rows, no parsing, no typo risk, and it works from a
phone. This module builds that board and dispatches a tap to the same code paths
the shell menu uses, so there is one implementation per action, not two.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# (label, callback-data) per section. Labels are the Telegram surface, so they
# carry the emoji and a verb rather than a tool name.
SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "🧠 AI",
        (
            ("💻 OpenCode", "ai:oc"),
            ("🛠 OpenHands", "ai:oh"),
            ("🦾 Hermes", "ai:hermes"),
            ("🧩 Kilo Code", "ai:kilo"),
        ),
    ),
    (
        "⚙️ Services",
        (
            ("📊 Status", "ops:status"),
            ("💾 Disk", "ops:disk"),
            ("🧠 Memory", "ops:mem"),
            ("🐳 Docker", "ops:docker"),
            ("🔧 Services", "ops:svc"),
        ),
    ),
    (
        "🛠 Server",
        (
            ("🩺 Health", "srv:health"),
            ("📜 Logs", "srv:logs"),
            ("🚀 Deploy", "srv:deploy"),
            ("💾 Backup", "srv:backup"),
            ("🖥 Control panel", "srv:panel"),
        ),
    ),
    (
        "🌐 Fleet",
        (
            ("📡 Fleet parity", "fleet:parity"),
            ("🖥 Box identity", "fleet:box"),
            ("🧰 Gate status", "fleet:gate"),
            ("🌐 MCP health", "fleet:mcp"),
            ("💸 Spend", "fleet:cost"),
        ),
    ),
)


# section label -> buttons, two per row
def _rows(buttons: tuple[tuple[str, str], ...]) -> list[list[InlineKeyboardButton]]:
    """Lay buttons out two per row, the Telegram convention."""
    return [
        [InlineKeyboardButton(label, callback_data=data) for label, data in buttons[i : i + 2]]
        for i in range(0, len(buttons), 2)
    ]


def build_menu() -> InlineKeyboardMarkup:
    """The full server-management board."""
    rows: list[list[InlineKeyboardButton]] = []
    for title, buttons in SECTIONS:
        rows.append([InlineKeyboardButton(title, callback_data="noop")])
        rows.extend(_rows(buttons))
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="menu:refresh")])
    return InlineKeyboardMarkup(rows)


def find_handler(
    callback_data: str,
) -> tuple[str, str] | None:
    """Resolve a callback to (namespace, action), or None when unknown.

    Keeping resolution in one place means a new button is added in SECTIONS and
    dispatched by namespace — the two cannot drift apart.
    """
    if callback_data == "menu:refresh":
        return ("menu", "refresh")
    if callback_data == "noop":
        # Section headers are non-interactive labels. Resolve them to a
        # no-op that re-renders the board instead of falling through to the
        # "Unhandled action" branch (dead button until 2026-10-16).
        return ("menu", "noop")
    namespace, _, action = callback_data.partition(":")
    known = {data for _, buttons in SECTIONS for _, data in buttons}
    if callback_data in known:
        return (namespace, action)
    return None


async def run_action(
    namespace: str,
    action: str,
    report: Callable[[str], Awaitable[Any]],
) -> str:
    """Execute one action and return the text to send back.

    Every branch is a shell call to the same helpers the menu uses, so a tap
    cannot drift from what the terminal shows. Failures become readable text
    rather than an exception escaping into the update loop.
    """
    commands: dict[tuple[str, str], str] = {
        ("ops", "status"): "systemctl is-active rmi-backend rmiadminbot reembed-8b",
        ("ops", "disk"): "df -h /",
        ("ops", "mem"): "free -h",
        ("ops", "docker"): "docker ps --format 'table {{.Names}}\\t{{.Status}}'",
        ("ops", "svc"): "systemctl list-units --type=service --state=failed --no-pager",
        (
            "srv",
            "health",
        ): 'python3 -c "from app.domains.telegram.adminbot.admin_health import quick_status; print(quick_status())"',
        ("srv", "logs"): "journalctl -u rmiadminbot -n 15 --no-pager",
        ("srv", "deploy"): "echo 'deploy is owner-gated; run: fleet-deploy --check'",
        (
            "srv",
            "backup",
        ): "python3 /root/scripts/hf-quota status 2>/dev/null || echo 'no backup ledger'",
        (
            "srv",
            "panel",
        ): 'python3 -c "from app.domains.telegram.adminbot.admin_health import control_panel; print(control_panel())"',
        ("fleet", "parity"): "fleet-parity-sync --check",
        ("fleet", "box"): "hostname -s; fleet-box-name; bash --version",
        ("fleet", "gate"): "systemctl list-timers --no-pager | grep -E 'fleet-parity|cc-reembed'",
        ("fleet", "mcp"): "curl -s --max-time 4 -o /dev/null -w 'context-crypto:%{http_code}\\n'"
        " http://127.0.0.1:9091/mcp",
        ("fleet", "cost"): "python3 /root/scripts/hf-quota check 2>/dev/null || echo 'n/a'",
    }
    if namespace == "ai":
        targets = {"oc": "opencode", "oh": "openhands", "hermes": "hermes", "kilo": "kilo"}
        tool = targets.get(action, "")
        return (
            f"🚀 `{tool}` is available through the fleet launcher.\n"
            f"   Use `/ai ask <prompt>` here, or start `{tool}` from the operator workstation."
        )
    command = commands.get((namespace, action))
    if command is None:
        return f"❓ Unknown action `{namespace}:{action}`"
    return str(await report(command))[:3500]
