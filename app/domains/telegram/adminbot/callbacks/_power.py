"""Power callbacks — engines + heavy lane + community console (P4/P5).

Dispatcher contract: ``async def handle(data, query) -> bool``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.domains.telegram.adminbot import engines, power_ops

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle engine_* / heavy_* / community_* callback data."""
    if data.startswith("engine_pick_"):
        engine_id = data.replace("engine_pick_", "", 1)
        ok = await engines.set_current(engine_id)
        spec = engines.ENGINES.get(engine_id)
        current = await engines.get_current()
        from app.domains.telegram.adminbot.engine_gateway import engine_keyboard

        if ok and spec:
            title = f"✅ Interaction setup → {spec.label} ({spec.hint})"
        elif spec:
            title = f"⚠️ {spec.label} selected, but could not persist (Redis down?)"
        else:
            title = f"⚠️ Unknown engine: {engine_id}"
        await query.edit_message_text(title, reply_markup=engine_keyboard(current))
        return True

    if data == "engines_menu":
        from app.domains.telegram.adminbot.engine_gateway import engine_keyboard

        current = await engines.get_current()
        label = engines.ENGINES[current].label if current in engines.ENGINES else current
        await query.edit_message_text(
            f"🤖 Pick your interaction setup:\nCurrent: {label}",
            reply_markup=engine_keyboard(current),
        )
        return True

    if data == "heavy_start":
        await query.edit_message_text("⏳ Starting heavy lane (~45s)…")
        await power_ops._systemctl("start", power_ops.HEAVY_UNIT, timeout=200)
        for _ in range(45):
            if await power_ops.heavy_health() is not None:
                break
            await asyncio.sleep(2)
        await query.edit_message_text(await power_ops.heavy_status_text(), reply_markup=power_ops.heavy_keyboard())
        return True

    if data == "heavy_stop":
        await power_ops._systemctl("stop", power_ops.HEAVY_UNIT)
        await query.edit_message_text(await power_ops.heavy_status_text(), reply_markup=power_ops.heavy_keyboard())
        return True

    if data == "heavy_status":
        await query.edit_message_text(await power_ops.heavy_status_text(), reply_markup=power_ops.heavy_keyboard())
        return True

    if data == "selftest_run":
        from app.domains.telegram.adminbot.selftest import (
            format_selftest,
            run_selftest,
            selftest_keyboard,
        )

        await query.edit_message_text("🩺 Running self-test…")
        results = await run_selftest()
        await query.edit_message_text(format_selftest(results), reply_markup=selftest_keyboard())
        return True

    if data == "community_status":
        await query.edit_message_text(
            await power_ops.community_status_text(),
            reply_markup=power_ops.community_keyboard(),
        )
        return True

    if data == "community_logs":
        text = await power_ops._journal("rugmunchbot", lines=30)
        await query.edit_message_text(
            f"📜 rugmunchbot — last 30 lines:\n\n{text[-3000:]}",
            reply_markup=power_ops.community_keyboard(),
        )
        return True

    if data == "community_restart":
        await power_ops._systemctl("restart", "rugmunchbot")
        await query.edit_message_text("🔄 Bot restarted.", reply_markup=power_ops.community_keyboard())
        return True

    return False
