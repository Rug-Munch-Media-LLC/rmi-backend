"""Message-building helpers for the chat router.

Extracted from ``app/routers/chat.py`` (2026-08-14) so the conversation
preparation logic lives in a focused module. ``_sanitize_history`` and
``_normalize_history`` are pure transformations; ``_build_messages`` is the
only helper here that touches I/O (it reads the shared chat-history DB when
no explicit ``history`` is passed).

The shared ``ai_guard`` instance lives here because ``_build_messages`` is its
main consumer; ``app.routers.chat`` re-imports the same instance so the routes
and the message builder share one guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.security.ai_guard import AIGuard

from ..chat_db import _get_db
from ._market_context import build_market_context_block
from ._prompts import SYSTEM_PROMPT

# Initialize guard (shared with the routes in ``chat.py``)
ai_guard = AIGuard()

CONTEXT_MAX_MESSAGES = 20


def _sanitize_history(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Whitelist roles (user/assistant only) + truncate content.

    Input is expected in chronological order (oldest → newest). Used for both
    DB-loaded history and explicit history passed by trusted callers.
    """
    history: list[dict[str, str]] = []
    for r in items:
        role = r.get("role", "")
        content = r.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if len(content) > 4000:
            content = content[:4000]
        history.append({"role": role, "content": content})
    return history


def _normalize_history(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge consecutive same-role turns and drop a trailing assistant turn.

    Safety net on top of ``ai_guard.build_safe_messages`` (which always
    appends the CURRENT user message as the LAST turn). Consecutive
    same-role turns (e.g. ``user, user``) can confuse small local models
    into answering the earlier message, and a trailing ``assistant`` turn
    from history would sit directly before the new user turn and invite the
    model to continue its prior answer instead of answering the new question.
    """
    merged: list[dict[str, str]] = []
    for item in items:
        role = item.get("role", "")
        content = item.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] = f"{merged[-1]['content']}\n\n{content}"
        else:
            merged.append({"role": role, "content": content})
    # A dangling trailing assistant turn would sit unanswered directly before
    # the current user message — drop it so the model answers the CURRENT
    # question instead of continuing the prior answer.
    if merged and merged[-1]["role"] == "assistant":
        merged.pop()
    return merged


async def _build_messages(
    user_id: str,
    message: str,
    system_context: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build message list with conversation history + market context + security hardening.

    ``history`` (explicit, chronological) OVERRIDES the DB-loaded history when
    provided — used by trusted server-side callers (e.g. the Telegram bot)
    that must not read the shared anonymous history bucket.
    """
    if history is None:
        # Fetch recent conversation history (newest-first → reverse to chronological)
        conn = _get_db()
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, CONTEXT_MAX_MESSAGES),
        ).fetchall()
        conn.close()
        history_items = _sanitize_history([{"role": r["role"], "content": r["content"]} for r in reversed(rows)])
    else:
        # Trusted explicit history → keep caller order, cap at context window.
        history_items = _sanitize_history(list(history)[-CONTEXT_MAX_MESSAGES:])

    # History safety net: merge consecutive same-role turns and drop a
    # dangling trailing assistant turn so the CURRENT user message is the
    # final turn the model answers (see _normalize_history). build_safe_messages
    # below always appends the current user message as the LAST message.
    history_items = _normalize_history(history_items)

    # Build system prompt with hardening + current date/year awareness.
    # Day-granularity date ONLY: minute-granularity stamps (the prior
    # "%Y-%m-%d %H:%M UTC") changed the prompt prefix on every message and
    # broke provider prefix caching. A day stamp is stable for the whole day.
    now = datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    system = SYSTEM_PROMPT.format(date=date_str, year=str(now.year))

    # ── Market context block (ambient market awareness for EVERY answer) ──
    # Injected here so both the website chat path and the Telegram bot path
    # (both call _build_messages) always carry live market context. Best-
    # effort: build_market_context_block() returns "" when the market sources
    # are down, so chat never waits on, breaks on, or fabricates this block.
    market_block = await build_market_context_block()
    if market_block:
        system += f"\n\n{market_block}"

    if system_context:
        # Escape any delimiter tokens in user context. Cap is sized for the
        # Telegram bot's PERSONA_CONTEXT block (~4.7k chars: grounding bullets
        # + anti-hallucination rules + response-formatting must reach the
        # model intact) plus room for server-side RAG/web context.
        safe_context = ai_guard._escape_delimiters(system_context[:6000])
        system += f"\n\nAdditional Context: {safe_context}"

    # ── Inert history (fixes "answers the wrong question") ──
    # Small local models, when handed history as real turns, frequently answer
    # an EARLIER user question instead of the current one. Render the prior
    # conversation as a single INERT reference block in the system prompt and
    # keep the CURRENT message as the only real user turn. Keep just the last
    # few turns (speed + less confusion).
    if history_items:
        block = "PREVIOUS CONVERSATION (reference only — do NOT answer these; answer ONLY the NEW question below):\n"
        for h in history_items[-6:]:
            role = h["role"].upper()
            block += f"[{role}] {h['content'][:400]}\n"
        system += f"\n\n{block}"

    # Use security-hardened message builder (current message is the only turn)
    messages = ai_guard.build_safe_messages(
        system_prompt=system,
        history=[],
        user_message=message,
        max_history=CONTEXT_MAX_MESSAGES,
    )
    return messages
