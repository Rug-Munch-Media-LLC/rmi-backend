"""Leaf actions behind the Telegram ops console.

Every entry in :data:`LEAVES` calls the same helper the shell/command surface
calls — one implementation per action, so a tap cannot drift from the terminal.
Mutating actions are two-step: the leaf renders a confirm keyboard and the
``<section>:do:<target>`` callback executes the audited helper.  Target names
are validated here, never trusted from callback data.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.adminbot import (
    _ops_backup,
    _ops_core,
    _ops_docker,
    _ops_system,
    alerts_feed,
)
from app.domains.telegram.adminbot.ai_keyboard import list_models
from app.domains.telegram.adminbot.alerts_render import QUEUE_KEYS
from app.domains.telegram.adminbot.redis_listener import (
    _build_redis_url,
    _resolve_redis_password,
)

if TYPE_CHECKING:
    from collections.abc import Callable

LEAF_TIMEOUT_S = 45.0
AI_HELP = (
    "Ask with /ai <prompt> (read-only) or /oc <task> for a tooled run.\n/models lists providers."
)
AUDIT_LOG = Path("/var/log/rmiadminbot/commands.jsonl")

#: One safe token — container/unit names, never a shell fragment.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.-]{0,63}")
_UNIT_PREFIXES = ("rmi", "llama", "super", "cc-", "fleet", "nginx")


class LeafResult(NamedTuple):
    """Rendered leaf output plus an optional keyboard (confirm / picker)."""

    text: str
    markup: InlineKeyboardMarkup | None = None


def _out(result: dict[str, Any]) -> str:
    """Flatten a ``_ops_core`` result dict into display text."""
    return str(result.get("output") or result.get("error") or "(no output)")


def _run(args: list[str], timeout: int = 15) -> str:
    """Run one argv list through the console's canonical subprocess helper."""
    return _out(_ops_core._run_subprocess(args, timeout=timeout))


def _journal() -> str:
    return _run(["journalctl", "-u", "rmiadminbot", "-n", "25", "--no-pager"], timeout=20)


def _picker(section: str, prompt: str, targets: list[str]) -> LeafResult:
    """Second-tap confirm keyboard for a mutation."""
    if not targets:
        return LeafResult(prompt)
    return LeafResult(
        prompt,
        InlineKeyboardMarkup(
            [[InlineKeyboardButton(t, callback_data=f"{section}:do:{t}")] for t in targets[:8]]
        ),
    )


def _docker_bodies() -> list[str]:
    return [
        n
        for n in _run(["docker", "ps", "--format", "{{.Names}}"], timeout=10).splitlines()
        if _NAME.fullmatch(n)
    ]


def _unit_names() -> list[str]:
    raw = _run(
        ["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain"],
        timeout=10,
    )
    names = [line.split()[0].split(".")[0] for line in raw.splitlines() if line.split()]
    return [n for n in names if n.startswith(_UNIT_PREFIXES)]


def _alerts_status() -> str:
    """Unread alert backlog per queue — proves the feed is draining."""
    import redis

    url = _build_redis_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"), _resolve_redis_password()
    )
    client = redis.from_url(url, socket_timeout=3)
    try:
        return "\n".join(f"{key}: {client.llen(key)}" for key in QUEUE_KEYS) or "(no queues)"
    except Exception as exc:  # noqa: BLE001 - surface the failure to the owner
        return f"redis unavailable: {type(exc).__name__}: {str(exc)[:150]}"
    finally:
        client.close()


def _diag() -> str:
    """The /diag machinery, budgeted to five read-only commands."""
    import time

    from app.domains.telegram.adminbot.cmd_audit.diag import _run_one
    from app.domains.telegram.adminbot.cmd_audit.format import format_diag

    async def _probe() -> str:
        started = time.monotonic()
        rows = [
            await _run_one(name, (), 4.0) for name in ("status", "disk", "dps", "uptime", "models")
        ]
        return format_diag(rows, time.monotonic() - started)

    return asyncio.run(_probe())


def _diag_audit() -> str:
    try:
        return (
            "\n".join(AUDIT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-12:])
            or "(empty)"
        )
    except OSError as exc:
        return f"{AUDIT_LOG}: {exc}"


LEAVES: dict[tuple[str, str], Callable[[], str | LeafResult]] = {
    ("srv", "status"): lambda: _ops_system.format_status(_ops_system.system_status()),
    ("srv", "disk"): lambda: _ops_system.format_disk(_ops_system.disk_usage()),
    ("srv", "mem"): lambda: _ops_system.format_memory(_ops_system.memory_report()),
    ("srv", "top"): lambda: "\n".join(
        _run(["ps", "-eo", "pcpu,pmem,comm", "--sort=-pcpu", "--no-headers"]).splitlines()[:12]
    ),
    ("srv", "units"): _ops_system.service_list,
    ("docker", "ps"): _ops_docker.docker_ps,
    ("docker", "logs"): lambda: _out(_ops_docker.docker_logs("rmi-backend", 40)),
    ("docker", "restart"): lambda: _picker(
        "docker", "🐳 Restart which container?", _docker_bodies()
    ),
    ("svc", "list"): _ops_system.service_list,
    ("svc", "logs"): _journal,
    ("svc", "restart"): lambda: _picker("svc", "⚙️ Restart which unit?", _unit_names()),
    ("backup", "status"): lambda: _ops_backup.format_backup_status(_ops_backup.backup_status()),
    ("backup", "verify"): lambda: _out(_ops_backup.backup_verify()),
    ("backup", "run"): lambda: _picker("backup", "💾 Run the full backup now?", ["full"]),
    ("alerts", "status"): _alerts_status,
    ("alerts", "config"): lambda: "\n".join(
        [*(f"queue: {q}" for q in QUEUE_KEYS), f"poll: {alerts_feed.POLL_TIMEOUT_SECONDS}s"]
    ),
    ("ai", "models"): list_models,
    ("ai", "help"): lambda: AI_HELP,
    ("diag", "run"): _diag,
    ("diag", "audit"): _diag_audit,
    ("diag", "log"): _journal,
}


async def _execute(section: str, target: str) -> LeafResult:
    """Run the confirmed mutation for ``<section>:do:<target>``."""
    if not _NAME.fullmatch(target or ""):
        return LeafResult(f"❌ Invalid target: {target[:40]!r}")
    if section == "docker":
        return LeafResult(_out(await asyncio.to_thread(_ops_docker.docker_restart, target)))
    if section == "svc":
        return LeafResult(
            _out(await asyncio.to_thread(_ops_system.service_control, target, "restart"))
        )
    if section == "backup" and target == "full":
        return LeafResult(_out(await asyncio.to_thread(_ops_backup.trigger_backup, target)))
    return LeafResult(f"❓ Nothing to confirm for {section}:{target}")


async def run_leaf(section: str, action: str) -> LeafResult:
    """Execute one leaf and return display text (never raises)."""
    if action.startswith("do:"):
        return await _execute(section, action[3:])
    leaf = LEAVES.get((section, action))
    if leaf is None:
        return LeafResult(f"❓ Unknown action: {section}:{action}")
    try:
        value = await asyncio.wait_for(asyncio.to_thread(leaf), timeout=LEAF_TIMEOUT_S)
    except TimeoutError:
        return LeafResult(f"⏱️ Timed out after {LEAF_TIMEOUT_S:.0f}s.")
    except Exception as exc:  # noqa: BLE001 - the owner sees it, the log keeps the trace
        return LeafResult(f"❌ {type(exc).__name__}: {str(exc)[:200]}")
    return value if isinstance(value, LeafResult) else LeafResult(str(value))
