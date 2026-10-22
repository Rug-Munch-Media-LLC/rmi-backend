"""Read-only command targets probed by /diag (imports stay lazy)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

#: (command, args) — read-only handlers only; nothing here mutates host state.
SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("start", ()),
    ("help", ()),
    ("menu", ()),
    ("commands", ()),
    ("status", ()),
    ("health", ()),
    ("disk", ()),
    ("mem", ()),
    ("uptime", ()),
    ("dps", ()),
    ("docker", ()),
    ("services", ()),
    ("svc_list", ()),
    ("top", ()),
    ("logs", ("rmi-backend",)),
    ("backup_status", ()),
    ("ragstats", ()),
    ("news", ()),
    ("scanstats", ()),
    ("indexer", ()),
    ("units", ()),
    ("gates", ()),
    ("dataops", ()),
    ("models", ()),
    ("engine", ()),
    ("selftest", ()),
)

_EXTRA = {
    "menu": ("app.domains.telegram.adminbot.tg_menu_handlers", "cmd_menu"),
    "models": ("app.domains.telegram.adminbot.chat.register", "on_models"),
    "engine": ("app.domains.telegram.adminbot.engine_gateway", "cmd_engine"),
    "units": ("app.domains.telegram.adminbot.control_commands", "cmd_units"),
    "gates": ("app.domains.telegram.adminbot.control_commands", "cmd_gates"),
    "dataops": ("app.domains.telegram.adminbot.control_commands", "cmd_dataops"),
}


def resolve(name: str) -> Callable[..., Any]:
    """Resolve one probe target by command name."""
    if name in _EXTRA:
        module_name, attr = _EXTRA[name]
        module = __import__(module_name, fromlist=[attr])
        return getattr(module, attr)
    from app.domains.telegram.adminbot import bot_commands as bc

    return getattr(bc, f"cmd_{name}")
