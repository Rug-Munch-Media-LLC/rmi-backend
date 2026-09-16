"""Engine commands — ``/engine`` (choose) and ``/ask`` (run through it).

The interaction setup is remembered (engine registry, Redis-backed) and can
be overridden per call: ``/ask <engine> | <prompt>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_audit, engines, owner_persona
from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

_ENGINE_HELP = (
    "🤖 *Engines*\n\n"
    "`/engine` — choose your interaction setup\n"
    "`/engine <id>` — switch directly\n"
    "`/ask <prompt>` — run a task through the *current* engine\n"
    "`/ask <engine> | <prompt>` — one-off override\n\n"
    "The setup is remembered across restarts."
)


def engine_keyboard(current: str) -> InlineKeyboardMarkup:
    """Inline keyboard of all engines with the current one check-marked."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for engine_id, spec in engines.ENGINES.items():
        mark = "✅ " if engine_id == current else ""
        warn = "" if engines.available(spec) else " ⚠️"
        row.append(InlineKeyboardButton(f"{mark}{spec.label}{warn}", callback_data=f"engine_pick_{engine_id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


@owner_only
async def cmd_engine(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or switch the current interaction setup."""
    if update.message is None:
        return
    args = ctx.args or []
    current = await engines.get_current()

    if args:
        target = args[0].lower()
        if target in engines.ENGINES:
            ok = await engines.set_current(target)
            spec = engines.ENGINES[target]
            if ok:
                admin_audit.log_action(
                    "engine_set",
                    detail=target,
                    user_id=update.effective_user.id if update.effective_user else None,
                )
                await update.message.reply_text(
                    f"✅ Interaction setup → *{spec.label}* ({spec.hint})",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text(
                    f"⚠️ Could not persist `{target}` (Redis down?) — using it anyway.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return
        await update.message.reply_text(
            f"Unknown engine `{args[0]}`. Pick below:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=engine_keyboard(current),
        )
        return

    current_spec = engines.ENGINES.get(current)
    label = current_spec.label if current_spec else current
    await update.message.reply_text(
        f"{_ENGINE_HELP}\nCurrent: *{label}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=engine_keyboard(current),
    )


@owner_only
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one prompt through the current engine (or an override)."""
    if update.message is None:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/ask <prompt>` — or `/ask <engine> | <prompt>`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw = " ".join(args)
    engine_id = await engines.get_current()
    prompt = raw
    if "|" in raw:
        maybe_engine, _, rest = raw.partition("|")
        candidate = maybe_engine.strip().lower()
        if candidate in engines.ENGINES and rest.strip():
            engine_id = candidate
            prompt = rest.strip()

    spec = engines.ENGINES.get(engine_id)
    label = spec.label if spec else engine_id
    msg = await update.message.reply_text(f"⏳ {label} working…")
    result = await engines.run_engine(engine_id, prompt, prepend=owner_persona.OWNER_PREAMBLE)

    status = "✅" if result["ok"] else "❌"
    seconds = result["latency_ms"] / 1000.0
    admin_audit.log_action(
        "ask",
        detail=f"engine={engine_id} ok={result['ok']} ms={int(result['latency_ms'])}",
        user_id=update.effective_user.id if update.effective_user else None,
    )
    output = result["output"]
    if len(output) > 3500:
        # Long output → full document + a short caption (better than truncation).
        import io

        buf = io.BytesIO(output.encode("utf-8"))
        buf.name = f"{engine_id}-output.txt"
        await msg.delete()
        await update.message.reply_document(
            buf,
            filename=f"{engine_id}-output.txt",
            caption=f"{status} {result.get('label', engine_id)} · {seconds:.1f}s · full output attached",
        )
        return
    # Plain text on purpose: engine output is arbitrary and must never be
    # parsed as Telegram markup.
    text = f"{status} {result.get('label', engine_id)} · {seconds:.1f}s\n\n{output[:3800]}"
    await msg.edit_text(text)
