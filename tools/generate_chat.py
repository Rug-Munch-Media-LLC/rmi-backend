"""Generate the admin-bot chat package and owner permission module.

Every module holds one function and stays under 50 lines; the generator asserts
both after writing, so the rule is mechanical rather than aspirational.

Run from the repo root:  python3 tools/generate_chat.py
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "domains" / "telegram" / "adminbot" / "chat"
BUDGET = 50

FILES: dict[str, str] = {}

FILES["__init__.py"] = '''"""Natural-language chat, model selection, and owner-scoped control."""
'''

FILES["owner.py"] = '''"""Owner gate for privileged actions."""

from __future__ import annotations

from app.domains.telegram.adminbot.config import OWNER_IDS


def is_owner(user_id: int | None) -> bool:
    """True when the sender is the owner.

    Owner resolution already has a three-source fallback (env, owner env var,
    secret store). Everything privileged funnels through this check so there is
    exactly one place that decides who may act.
    """
    return bool(user_id) and int(user_id) in OWNER_IDS
'''

FILES["audit.py"] = '''"""Record every privileged action before it runs."""

from __future__ import annotations

import logging

from app.domains.telegram.adminbot.admin_audit import log_action

logger = logging.getLogger("adminbot.chat.audit")


def record(action: str, detail: str, user_id: int | None) -> None:
    """Write an audit entry, never raising into the caller's control flow.

    Auditing must not be able to block the action it describes, so failures are
    logged and swallowed; the audit log itself is append-only.
    """
    try:
        log_action(action, detail, user_id)
    except Exception:  # audit must never break the control path
        logger.warning("audit write failed for %s", action, exc_info=True)
'''

FILES["owner_commands.py"] = '''"""Commands the owner may run that ordinary users may not."""

from __future__ import annotations

import shlex

from app.domains.telegram.adminbot.admin_terminal import _ALLOWED_COMMANDS

# Mutating or privileged verbs. Anyone may run the read-only set already in
# _ALLOWED_COMMANDS; only the owner reaches these, and only after an audit entry.
OWNER_ONLY: frozenset[str] = frozenset({
    "systemctl", "docker", "git", "deploy", "install", "apt", "apt-get", "dnf",
    "yum", "pacman", "pip", "uv", "npm", "pnpm", "bun", "make", "bash", "sh",
    "python", "python3", "mv", "cp", "rm", "mkdir", "touch", "chmod", "chown",
    "kill", "pkill", "reboot", "shutdown", "ufw", "iptables", "nft", "journalctl",
})


def allowed_for(command: str, owner: bool) -> bool:
    """True when `command` may run, widening the set only for the owner.

    A compound line is checked segment by segment, so `ls; reboot` cannot smuggle
    a privileged verb past the read-only allowlist.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    for segment in " ".join(parts).replace("&&", ";").replace("|", ";").split(";"):
        words = [w for w in segment.split() if w and not w.startswith("-")]
        if not words:
            continue
        base = words[0].rsplit("/", 1)[-1]
        if base in _ALLOWED_COMMANDS:
            continue
        if owner and base in OWNER_ONLY:
            continue
        return False
    return True
'''

FILES["intents.py"] = '''"""Map a plain-language message onto an existing bot command."""

from __future__ import annotations

import re

NL = chr(10)

# Ordered: the first matching pattern wins, so put the specific phrases first.
ROUTES: tuple[tuple[str, str], ...] = (
    (r"\\b(restart|reboot)\\b.*\\b(\\w+)", "svc {1} restart"),
    (r"\\b(stop|halt)\\b.*\\b(\\w+)", "svc {1} stop"),
    (r"\\b(start|run)\\b.*\\b(\\w+)", "svc {1} start"),
    (r"\\b(logs?|journal)\\b.*\\b(\\w+)", "svc {1} logs"),
    (r"\\b(deploy|release)\\b", "deploy"),
    (r"\\b(disks?|storage)\\b", "disk"),
    (r"\\b(memory|ram)\\b", "mem"),
    (r"\\b(docker|containers?)\\b", "dps"),
    (r"\\b(git)\\b", "git"),
    (r"\\b(models?|engine|llm)\\b", "__models__"),
    (r"\\b(health|status|how.*(going|it))\\b", "status"),
    (r"\\b(uptime|load)\\b", "uptime"),
    (r"\\b(alerts?|warnings?)\\b", "alerts"),
    (r"\\b(backups?)\\b", "admin_backup_status"),
)


# Intents any allowed user may run. Everything else is owner-only.
OPEN_INTENTS: frozenset[str] = frozenset(
    {"status", "disk", "mem", "dps", "uptime", "alerts", "__models__"}
)


def classify(text: str) -> str | None:
    """Return the command to run for `text`, or None if it is not a request.

    Only phrases in ROUTES are translated. Free-form questions are left to the
    assistant path rather than guessed at a server command.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    for pattern, template in ROUTES:
        match = re.search(pattern, lowered)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        return template.format(*groups) if groups else template
    return None
'''

FILES["lanes.py"] = '''"""Model lanes the picker can offer, with their live endpoints."""

from __future__ import annotations

from typing import Final

LANES: Final[tuple[dict[str, str], ...]] = (
    {"id": "local-workhorse", "label": "Qwen3.6-35B (local :8009)", "url": "http://127.0.0.1:8009"},
    {"id": "local-fast", "label": "Qwythos-9B (local :8009 fast)", "url": "http://127.0.0.1:8009"},
    {"id": "zerogpu", "label": "ZeroGPU fleet (:20201)", "url": "http://127.0.0.1:20201"},
    {"id": "litellm", "label": "LiteLLM router (:4000)", "url": "http://127.0.0.1:4000"},
    {"id": "opencode", "label": "opencode", "url": ""},
    {"id": "kilocode", "label": "KiloCode", "url": ""},
    {"id": "openhands", "label": "OpenHands", "url": ""},
    {"id": "hermes", "label": "Hermes", "url": ""},
)


def lane_ids() -> tuple[str, ...]:
    """Return every selectable lane id."""
    return tuple(lane["id"] for lane in LANES)
'''

FILES["models_discover.py"] = '''"""Report which model lanes are actually answering."""

from __future__ import annotations

import urllib.error
import urllib.request

from .lanes import LANES

TIMEOUT = 4


def discover() -> list[dict[str, object]]:
    """Return each lane with an `up` flag for the menu.

    A lane that is paused, down, or needs auth still counts as selectable for
    the CLI engines (opencode, kilo, openhands, hermes) and shows as offline for
    the HTTP ones, so the menu reports state instead of hiding options.
    """

    def up(url: str) -> bool:
        if not url:
            return True
        try:
            with urllib.request.urlopen(f"{url}/v1/models", timeout=TIMEOUT) as response:  # noqa: S310 - fixed http loopback lanes
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    return [{"id": lane["id"], "label": lane["label"], "up": up(lane["url"])} for lane in LANES]
'''

FILES["models_keyboard.py"] = '''"""Build the model picker keyboard."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build(models: list[dict[str, object]]) -> InlineKeyboardMarkup:
    """One button per lane; a dot marks the lanes that are answering.

    Callback data is the lane id, so a stale button can only ever select a lane
    that still exists in LANES.
    """
    rows = [
        [
            InlineKeyboardButton(
                f"{'🟢' if m['up'] else '⚪️'} {m['label']}",
                callback_data=f"model:{m['id']}",
            )
        ]
        for m in models
    ]
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="model:refresh")])
    return InlineKeyboardMarkup(rows)
'''

FILES["models_current.py"] = '''"""Read the owner's selected model lane."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

KEY = "selected_model"
DEFAULT = "local-workhorse"


def current(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return the selected lane for this chat, defaulting to the workhorse."""
    return (context.user_data or {}).get(KEY, DEFAULT)
'''

FILES["models_choose.py"] = '''"""Record the owner's model choice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .lanes import LANES
from .models_current import KEY

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def choose(context: ContextTypes.DEFAULT_TYPE, lane_id: str) -> str:
    """Record the choice and return a human label.

    An unknown lane is rejected rather than stored, so a stale button cannot
    leave the bot pointed at something that no longer exists.
    """
    known = {lane["id"]: lane["label"] for lane in LANES}
    label = known.get(lane_id)
    if label is None:
        return "Unknown model"
    context.user_data[KEY] = lane_id
    return label
'''

FILES["on_model.py"] = '''"""Handle model-picker button presses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .audit import record
from .models_choose import choose
from .models_discover import discover
from .models_keyboard import build
from .owner import is_owner

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = logging.getLogger("adminbot.chat.models")


async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledge immediately, then render the picker or confirm a choice."""
    query: CallbackQuery = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        await query.edit_message_text("🔒 Owner only.")
        return
    _, _, lane_id = (query.data or "::").partition(":")
    if lane_id == "refresh":
        await query.edit_message_text("🤖 Models", reply_markup=build(discover()))
        return
    label = choose(context, lane_id)
    record("model.select", label, query.from_user.id)
    await query.edit_message_text(f"✅ Model set: <b>{label}</b>")
'''

FILES["on_models_cmd.py"] = '''"""Handle /models."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update

from .models_discover import discover
from .models_keyboard import build
from .owner import is_owner


async def on_models(update: Update, context: object) -> None:
    """Render the picker for the owner, or a refusal for anyone else."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only.")
        return
    await update.message.reply_text("🤖 Select a model lane", reply_markup=build(discover()))
'''

FILES["on_text.py"] = '''"""Handle plain-language messages as commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .audit import record
from .intents import OPEN_INTENTS, classify
from .owner import is_owner

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("adminbot.chat.text")


async def on_text(update: Update, context: object) -> None:
    """Translate a plain request into an existing command, or ask for clarity.

    Nothing is executed here: the translated text is handed back for the bot's
    own dispatcher, so the existing guards and handlers remain the only path to
    an action. A request the owner may not run is refused and audited.
    """
    text = update.message.text or ""
    command = classify(text)
    if command is None:
        await update.message.reply_text("🤖 Try /help, or ask: 'restart nginx'.")
        return
    if command not in OPEN_INTENTS and not is_owner(update.effective_user.id):
        record("chat.denied", command, update.effective_user.id)
        await update.message.reply_text("🔒 Owner only.")
        return
    record("chat.route", command, update.effective_user.id)
    await update.message.reply_text(f"▶️ <code>{command}</code>")
'''

FILES["on_text_handler.py"] = '''"""The plain-language text handler."""

from __future__ import annotations

from telegram.ext import MessageHandler, filters

from .on_text import on_text


def handler() -> MessageHandler:
    """Return the text handler, registered last so explicit commands win."""
    return MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
'''

FILES["register.py"] = '''"""Register the chat handlers on the existing application."""

from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from .on_model import on_pick
from .on_models_cmd import on_models
from .on_text_handler import handler as text_handler


def register(app: Application) -> None:
    """Attach model picker and natural-language handlers.

    Called from the bot's existing registration path. The text handler is added
    last so explicit commands keep priority over plain-language routing.
    """
    app.add_handler(CommandHandler("models", on_models))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^model:"))
    app.add_handler(text_handler())
'''

FILES["__main___check.py"] = "__SENTINEL__"


def write_all() -> int:
    """Write every module and assert the structural rules."""
    PKG.mkdir(parents=True, exist_ok=True)
    for name, body in FILES.items():
        if body == "__SENTINEL__":
            continue
        (PKG / name).write_text(body)
    problems: list[str] = []
    for path in sorted(PKG.glob("*.py")):
        text = path.read_text()
        if path.name == "__init__.py":
            continue
        count = len(re.findall(r"^def |^async def ", text, re.M))
        lines = len(text.splitlines())
        if count != 1:
            problems.append(f"{path.name}: {count} functions (want 1)")
        if lines >= BUDGET:
            problems.append(f"{path.name}: {lines} lines (budget {BUDGET})")
    for problem in problems:
        print("  " + problem)
    if problems:
        return 1
    print(f"generated {len(FILES) - 1} chat modules, one function each, under {BUDGET} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(write_all())
