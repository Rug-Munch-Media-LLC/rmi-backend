"""Slash commands for the server-control surface (/units, /gates, /dataops).

Kept separate from the callback router so each module stays under the file
cap and has one job: render a screen. The callback router owns the taps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.domains.telegram.adminbot import control_ops
from app.domains.telegram.adminbot.control_keyboards import (
    data_ops_keyboard,
    gates_keyboard,
    units_keyboard,
)
from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

UNITS_TEXT = "🖥 <b>Fleet Units</b> — tap a unit to control it."
GATES_TEXT = "🚦 <b>Gates</b> — last result per guard."
DATAOPS_TEXT = "🗄 <b>Data Ops</b> — each trigger asks for confirmation."


def _fmt(result: dict, limit: int = 3000) -> str:
    body = result.get("output") or result.get("error") or "(no output)"
    return str(body)[:limit]


@owner_only
async def cmd_units(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the fleet-unit picker."""
    if update.message is None:
        return
    await update.message.reply_text(UNITS_TEXT, parse_mode=ParseMode.HTML, reply_markup=units_keyboard())


@owner_only
async def cmd_gates(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the gates screen."""
    if update.message is None:
        return
    result = control_ops.gate_status()
    await update.message.reply_text(
        f"{GATES_TEXT}\n<pre>{_fmt(result)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=gates_keyboard(),
    )


@owner_only
async def cmd_dataops(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the data-ops picker."""
    if update.message is None:
        return
    await update.message.reply_text(DATAOPS_TEXT, parse_mode=ParseMode.HTML, reply_markup=data_ops_keyboard())


def register_commands(app) -> None:
    """Attach the three control slash commands in the pre-dispatch group."""
    app.add_handler(CommandHandler("units", cmd_units), group=-1)
    app.add_handler(CommandHandler("gates", cmd_gates), group=-1)
    app.add_handler(CommandHandler("dataops", cmd_dataops), group=-1)
