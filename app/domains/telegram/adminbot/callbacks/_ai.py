"""
Admin Bot - Callback handlers: AI / OpenCode
==============================================
OpenCode helpers, AI model menu, /oc confirmation gate, model switching.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.adminbot.helpers import back_button

try:
    from app.domains.telegram.adminbot import admin_ai
except ImportError:  # pragma: no cover
    admin_ai = None  # type: ignore[assignment]  # optional module

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle AI/OpenCode callback data. Returns True when matched."""
    # ── v4: AI section helpers ──
    if data == "oc_help":
        from app.domains.telegram.adminbot import admin_ai as _admin_ai

        kb = _admin_ai.model_keyboard() if _admin_ai and hasattr(_admin_ai, "model_keyboard") else None
        await query.edit_message_text(
            "🤖 *OpenCode Integration*\n\n"
            "`/oc <prompt>` — Run any prompt through OpenCode\n"
            "`/oc task <task>` — Quick admin task\n"
            "`/oc model` — View/switch model",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )
        return True
    elif data == "ai_menu":
        if admin_ai is None:
            await query.edit_message_text("⚠️ AI module not available.", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(
                admin_ai.list_models(),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_ai.model_keyboard(),
            )
        return True

    # ── v5: /oc tooled confirmation gate (hardened 2026-08-13) ──
    elif data == "oc_confirm":
        if admin_ai is None:
            await query.edit_message_text("⚠️ AI module not available.", parse_mode=ParseMode.MARKDOWN)
            return True
        entry = admin_ai.pop_oc_prompt(query.from_user.id)
        if entry is None:
            await query.edit_message_text(
                "⚠️ Confirmation expired or no pending `/oc` run. Re-run `/oc`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return True
        prompt = entry.get("prompt", "")
        is_task = bool(entry.get("task", False))
        if admin_audit is not None:
            admin_audit.log_action(
                "ai.opencode.tools",
                f"{'task' if is_task else 'prompt'}: {prompt[:200]}",
                query.from_user.id,
            )
        await query.edit_message_text("🤖 Running tooled OpenCode (confirmed)...")
        result = admin_ai.run_quick_task(prompt) if is_task else admin_ai.run_opencode(prompt, max_turns=3)
        if result["ok"]:
            output = result["output"][:3500]
            model_used = result.get("model", "unknown")
            duration = result.get("duration", 0)
            await query.edit_message_text(
                f"🤖 *OpenCode* (`{model_used}`, {duration:.1f}s)\n\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
        else:
            await query.edit_message_text(
                f"❌ OpenCode Error: {result.get('error', 'unknown')[:500]}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
        return True
    elif data == "oc_cancel":
        if admin_ai is not None:
            admin_ai.cancel_oc_prompt(query.from_user.id)
        await query.edit_message_text("❌ OpenCode run cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── AI callbacks ──
    elif data.startswith("ai_model_"):
        if admin_ai is None:
            await query.edit_message_text("⚠️ AI module not available.")
            return True
        model_id = data.replace("ai_model_", "")
        admin_ai.set_model(query.from_user.id, model_id)
        model_name = admin_ai.MODELS.get(model_id, {}).get("name", model_id)
        await query.edit_message_text(
            f"🤖 Switched to *{model_name}*\n`{model_id}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
        return True
    elif data == "ai_clear":
        if admin_ai:
            admin_ai.clear_history(query.from_user.id)
        await query.edit_message_text("🧹 Chat history cleared.", parse_mode=ParseMode.MARKDOWN)
        return True

    return False
