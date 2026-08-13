#!/usr/bin/env python3
"""
RugMunchBot -- SINGLE free-text AI chat pipeline (merged, v5+).

This module is the ONE pipeline for every free-text message plus the
/chat and /ask commands. It was created by merging the old
``handlers/message.py`` free-text pipeline into the backend RAG-wired chat
handler so no feature is shadowed by a duplicate registration.

What was merged here (from the old ``handlers/message.py``):
  * Owner admin console (persistent reply keyboard + one-tap /admin_* routing)
  * Bot kill-switch gate (``db.get_bot_state("bot_enabled")``)
  * Known-scam address block (``anti_abuse.is_known_scam_address``) before
    any scan / AI call
  * Bare contract address auto-detect -> full /scan (paste-an-address UX)
  * ``$TOKEN`` symbol lookup -> instant DexScreener price card
  * Tier-2 local intent matching (canned replies, zero backend cost)
  * Tier-3 friendly redirect (catch-all help card, flood-refunded)
  * Richer prompt-injection warnings (category-specific, with buttons)

What was kept from the old ``chat_ai.py``:
  * Backend ``POST /api/v1/chat`` call as the TIER-1 synthesis path (auto-RAG
    with ``news_v2`` grounding, real ``rag_count`` from ``X-RAG-Context``,
    per-user history via the bot SQLite DB, per-user rate limiting via
    ``X-Forwarded-For``)
  * Zen/local LLM fallbacks when the backend is unavailable (P0.2: backend
    first, best available grounding wins)
  * Address-in-question enhancement (scan risk summary prepended to the
    prompt, best-effort)
  * Gibberish gate, weekly quota, flood control, chunked HTML-safe answers
  * Bridge citations (P0.1): ``_fetch_bridge_context`` returns
    ``(blocks, sources, actions)``; the Zen/local fallback reports
    ``rag_count = len(sources)``, the answer surfaces up to 3 source lines,
    and bridge actions render as inline buttons.

Pipeline order (free text, ``ai_chat_handler``):
  1. guards (user / banned / spam) + ``record_activity(msg=True)``
  2. owner admin console (private DMs, runs before the kill-switch)
  3. bot kill-switch
  4. ``track_message()`` analytics
  5. prompt-injection block
  6. structural routing (NO AI quota): known-scam block -> bare address
     /scan -> ``$TOKEN`` price card
  7. ``chat_ai_response`` (shared AI core): chat_enabled gate -> injection ->
     weekly quota -> flood -> gibberish (tier-2/3) -> backend RAG chat
     (tier-1) -> Zen -> local llama.cpp fallback

``chat_ai_response`` is shared verbatim by /chat (handlers/chat.py) and
/ask (handlers/ask.py), so every AI answer flows through the same backend
synthesis path.

Deprecated modules after this merge:
  * ``ai_chat.py`` (bot-side RAG helpers) -- no longer imported anywhere.
  * ``handlers/message.py`` -- reduced to the admin reply-keyboard helpers
    still used by ``handlers/scanning.py`` (cmd_start).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from html import escape
from typing import TYPE_CHECKING, Any, cast

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.admin_state import track_ai_query, track_message, track_tokens
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.daily_context import get_daily_context
from app.domains.telegram.rugmunchbot.flood_control import check_flood, refund_flood
from app.domains.telegram.rugmunchbot.formatters import (
    TOKEN_RE,
    detect_chain,
    footer_links,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import (
    _CHAT_DISABLED,
    _FALLBACK_MESSAGE,
    _INJECTION_MESSAGES,
    _KNOWN_SCAM_WARNING,
    _REDIRECT_MESSAGE,
    _SLOW_DOWN,
    _STILL_DIGGING,
    CHUNK_MAX_CHARS,
    MAX_ADDRESS_HINTS,
    _beautify_answer,
    _chunk_text,
    _extract_addresses,
    _format_scan_summary,
    _is_capability_question,
    _is_deep_question,
    _is_greeting_or_thanks,
    _is_meaningful_question,
    _is_section_header,
    _markdown_bold_to_html,
    _split_oversized,
    local_ai_response,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm import (
    CHAT_ENDPOINT,  # re-exported (public __all__ / tests)
    CHAT_TIMEOUT,  # re-exported (public __all__)
    _call_backend_chat,  # re-exported (tests import from package)
    _call_local_llm,
    _call_zen_llm,
)
from app.domains.telegram.rugmunchbot.handlers.message import (
    admin_kb_is_sent,
    admin_kb_mark_sent,
    admin_kb_reset,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    main_menu_kb,
    owner_admin_reply_kb,
    paywall_kb,
)
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — BACKEND / BRIDGE
# ══════════════════════════════════════════════
# Backend RAG chat (tier-1): CHAT_ENDPOINT / CHAT_TIMEOUT live in _llm.py
# next to _call_backend_chat and are re-exported below (see the import block).
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit

# Chat RAG Bridge — RugBrain/RugMunchScan context + SearXNG web search.
# One call returns live web search, RAG knowledge, market digest, scanner
# context and exploit rundown blocks (fail-open upstream).
BRIDGE_CONTEXT_ENDPOINT = os.getenv("BRIDGE_CONTEXT_ENDPOINT", "http://127.0.0.1:8095/chat/context")
BRIDGE_TIMEOUT = 12.0  # web search can take a few seconds; never block chat on it


# ══════════════════════════════════════════════
# OWNER ADMIN CONSOLE (ported from handlers/message.py)
# Persistent reply keyboard pinned below the input box in private chats.
# Button labels are plain text; they are routed to the matching /admin_*
# command so @cryptorugmunch always has one-tap admin access.
# ══════════════════════════════════════════════
_ADMIN_BUTTON_MAP: dict[str, str] = {
    "\U0001f6e1\ufe0f Admin Dashboard": "/admin",
    "\U0001f4ca Stats": "/admin_stats",
    "\U0001f4e2 Broadcast": "/admin_broadcast",
    "\U0001f465 User Lookup": "/admin_user",
    "\U0001f504 Bot ON/OFF": "/admin_toggle",
    "\U0001f4ac Chat ON/OFF": "/admin_toggle_chat",
    "\U0001f3d9\ufe0f Main Menu": "/start",
}


async def _ensure_admin_reply_kb(update: Update) -> None:
    """Attach the persistent admin reply keyboard once per owner DM."""
    chat = update.effective_chat
    if chat is None or admin_kb_is_sent(chat.id) or update.message is None:
        return
    admin_kb_mark_sent(chat.id)
    try:
        await update.message.reply_text(
            "\U0001f6e1\ufe0f <b>Admin console</b>\n"
            "Tap a button below for quick admin actions \u2014 they stay pinned here.",
            parse_mode=ParseMode.HTML,
            reply_markup=owner_admin_reply_kb(),
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        admin_kb_reset(chat.id)
        logger.debug("admin reply keyboard send failed", exc_info=True)


async def _dispatch_admin_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Route an admin reply-keyboard button press to its /admin_* command."""
    from app.domains.telegram.rugmunchbot.handlers.owner import (
        cmd_admin,
        cmd_admin_broadcast,
        cmd_admin_stats,
        cmd_admin_toggle,
        cmd_admin_toggle_chat,
        cmd_admin_user,
    )
    from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_start

    cmd = _ADMIN_BUTTON_MAP[text]
    ctx.args = []
    if cmd == "/admin":
        await cmd_admin(update, ctx)
    elif cmd == "/admin_stats":
        await cmd_admin_stats(update, ctx)
    elif cmd == "/admin_broadcast":
        await cmd_admin_broadcast(update, ctx)
    elif cmd == "/admin_user":
        await cmd_admin_user(update, ctx)
    elif cmd == "/admin_toggle":
        await cmd_admin_toggle(update, ctx)
    elif cmd == "/admin_toggle_chat":
        await cmd_admin_toggle_chat(update, ctx)
    elif cmd == "/start":
        # /start re-attaches the persistent keyboard below the new message.
        admin_kb_reset(update.effective_chat.id)  # type: ignore[union-attr]  # PTB None-able fields
        await cmd_start(update, ctx)


async def _fetch_scan_context(address: str) -> str | None:
    """Best-effort quick scanner read → compact risk summary line.

    ``POST /api/v1/bot/scan`` is Redis-cached and fast; on any failure or
    missing data it returns ``None`` so chat never blocks on the scanner.
    """
    chain = detect_chain(address)
    try:
        async with httpx.AsyncClient(timeout=SCAN_TIMEOUT) as client:
            r = await client.post(
                SCAN_ENDPOINT,
                headers=backend_headers(),
                json={"address": address, "chain": chain},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("scan context unavailable (addr=%.16s): %s", address, e)
        return None
    if r.status_code != 200:
        logger.debug("scan context HTTP %d (addr=%.16s)", r.status_code, address)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict) or raw.get("error"):
        return None
    data = cast("dict[str, Any]", raw)
    return _format_scan_summary(address, data)


async def _fetch_bridge_context(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]] | None:
    """Live context from the Chat RAG Bridge (port 8095, fail-open).

    Returns ``(blocks, sources, actions)``:
      * ``blocks`` — one compact block combining the bridge's upstream
        context: SearXNG web search (live internet), RAG knowledge, market
        digest, token scanner and exploit rundown.
      * ``sources`` — the bridge's grounding citation metadata
        (``[{"type": "web"|"rag"|"scanner"|"market", ...}]``) used to render
        the real "Grounded in N intelligence sources" footer + source lines.
      * ``actions`` — suggested inline-button actions (deep-dive on a token,
        exploit rundown) built by the bridge's rundown block.

    ``None`` on any failure so chat never blocks on it.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(BRIDGE_TIMEOUT, connect=3.0, read=BRIDGE_TIMEOUT)) as client:
            r = await client.post(
                BRIDGE_CONTEXT_ENDPOINT,
                json={"message": message[:8000], "history": history},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("bridge context unavailable (user msg %.40s): %s", message, e)
        return None
    if r.status_code != 200:
        logger.debug("bridge context HTTP %d (user msg %.40s)", r.status_code, message)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    blocks = raw.get("blocks") if isinstance(raw.get("blocks"), dict) else {}
    if not blocks:
        return None
    # Order matters: scanner (address-specific) > web (live) > rag > market.
    parts: list[str] = []
    for key in ("scanner_context", "web_context", "rag_context", "market_context", "rundown_context"):
        val = (blocks.get(key) or "").strip()
        if val:
            # Strip meta-instructions the bridge embeds for ITS OWN llama call
            # ("introduce these with 'I found this on the web:'") — those leak
            # into our prompt and make the model parrot context instead of
            # answering. Keep the data, drop the stage directions.
            val = re.sub(
                r"\[[^\]]*\]\s*Fresh web results \(current[^)]*\)[:\-]?\s*",
                "[Web results (live)]\n",
                val,
            )
            val = re.sub(
                r"introduce these with ['\"]I found this on the web:['\"]\s*",
                "",
                val,
            )
            val = re.sub(r"\n{3,}", "\n\n", val).strip()
            if val:
                parts.append(val)
    if not parts:
        return None

    raw_sources = raw.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    sources = [s for s in sources if isinstance(s, dict)]
    raw_actions = raw.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    actions = [a for a in actions if isinstance(a, dict)]

    return "\n\n".join(parts), sources, actions


async def _gather_fallback_context(
    daily_ctx_task: asyncio.Task[Any],
    scan_ctx_task: asyncio.Task[Any] | None,
    bridge_ctx_task: asyncio.Task[Any],
) -> tuple[Any, Any, Any]:
    """Await the fallback context fetches (fail-open) for the Zen/local path.

    The backend tier-1 call runs CONCURRENTLY with these tasks and is awaited
    FIRST; this helper is only invoked when the backend returned ``None`` and
    the fallback path actually needs the bridge/daily/scan context. Exceptions
    are swallowed per-task (``return_exceptions=True``) so a failing fetcher
    degrades to ``None`` instead of killing the fallback LLM call.
    """
    tasks: list[asyncio.Task[Any]] = [daily_ctx_task, bridge_ctx_task]
    if scan_ctx_task is not None:
        tasks.append(scan_ctx_task)
    results = [
        r if not isinstance(r, BaseException) else None for r in await asyncio.gather(*tasks, return_exceptions=True)
    ]
    daily_context = results[0]
    bridge_ctx = results[1]
    scan_summary = results[2] if scan_ctx_task is not None else None
    return daily_context, scan_summary, bridge_ctx


def _render_bridge_actions(
    actions: list[dict[str, str]] | None,
) -> InlineKeyboardMarkup | None:
    """Map bridge suggested actions to Telegram inline buttons.

    Only existing, safe bot surfaces are wired: ``analyze`` targets become
    full-scan buttons (callback ``scan_<addr>_<chain>`` — the same convention
    as the rest of the bot). Unknown action types are skipped rather than
    rendering dead buttons. Returns ``None`` when there is nothing to show.
    """
    if not actions:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        action = str(a.get("action") or "")
        label = str(a.get("label") or "")
        target = str(a.get("target") or "")
        if action == "analyze" and target:
            chain = detect_chain(target)
            rows.append([InlineKeyboardButton(label, callback_data=f"scan_{target}_{chain}")])
        # exploit / other action types have no dedicated bot surface yet — skip.
    return InlineKeyboardMarkup(rows) if rows else None


def _append_source_lines(
    answer: str,
    sources: list[dict[str, str]] | None,
    limit: int = 3,
) -> str:
    """Append up to ``limit`` compact citation lines from the bridge sources.

    Only ``web`` and ``rag`` sources carry a query worth citing; scanner /
    market metadata lines are skipped (the answer already attributes them).
    No URLs are fabricated — bridge sources carry type+query metadata, and
    the grounding rules already make the model cite outlet/headline inline.
    Plain text only: ``_send_answer`` HTML-escapes every chunk.
    """
    if not sources:
        return answer
    lines: list[str] = []
    for s in sources:
        if len(lines) >= limit:
            break
        if not isinstance(s, dict):
            continue
        stype = str(s.get("type") or "")
        query = str(s.get("query") or "").strip()
        if stype == "web" and query:
            lines.append(f"\U0001f310 Web: {query[:90]}")
        elif stype == "rag" and query:
            lines.append(f"\U0001f4da RAG: {query[:90]}")
    if not lines:
        return answer
    return f"{answer}\n\n\u2014\n" + "\n".join(lines)


async def _injection_warning(update: Update, category: str) -> None:
    """Reply to a prompt-injection attempt with a targeted warning."""
    text = _INJECTION_MESSAGES.get(category, "I couldn't process that message. Ask me a normal question instead.")
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f50d /scan — Audit a token", callback_data="run_scan")],
            [InlineKeyboardButton("\U0001f4ac /chat — Ask me anything", callback_data="run_chat")],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    try:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\U0001f6e1\ufe0f <b>RugMunch Anti-Abuse</b>\n\n{text}\n\n<i>Message blocked: {category}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("injection warning send failed", exc_info=True)


async def _scam_address_warning(update: Update, address: str) -> None:
    """Reply when the user shares a known scam address."""
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f50d /report — Report a scam", callback_data="run_report")],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    try:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"{_KNOWN_SCAM_WARNING}\n\n"
            f"\U0001f517 Address: <code>{address}</code>\n\n"
            f"Already flagged one like this? /report helps us build the denylist.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("scam address warning send failed", exc_info=True)


async def _keep_typing(chat: Any) -> None:
    """Keep the Telegram typing indicator alive until cancelled.

    Telegram expires send_action('typing') after ~5 seconds. LLM calls take
    10-30s on CPU, so we re-send every 4s to keep the bubble visible.
    """
    while True:
        with contextlib.suppress(Exception):
            await chat.send_action("typing")
        try:
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            break


async def _edit_placeholder_if_slow(placeholder: Message, delay: float = 5.0) -> None:
    """After ``delay`` seconds, swap the thinking placeholder for a
    "still digging" note so long backend queries don't look stalled.

    The task is cancelled by the caller the moment the backend answers, so a
    fast response never gets clobbered by the interim edit.
    """
    try:
        await asyncio.sleep(delay)
        await placeholder.edit_text(_STILL_DIGGING, parse_mode=ParseMode.HTML)
    except (asyncio.CancelledError, TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai interim placeholder edit skipped", exc_info=True)


# ══════════════════════════════════════════════
# $TOKEN SYMBOL LOOKUP (ported from handlers/message.py)
# ══════════════════════════════════════════════


async def _lookup_token_symbol(text: str, message: Message) -> bool:
    """Instant ``$TOKEN`` price card via DexScreener.

    Returns True when a price card was sent; False when there is no symbol
    or the lookup failed so the caller falls through to the AI pipeline
    (matches the old handle_message behavior).
    """
    token_match = TOKEN_RE.search(text)
    if not token_match:
        return False
    symbol = token_match.group(1).upper()
    with contextlib.suppress(Exception):
        await message.chat.send_action("typing")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = float(p.get("volume", {}).get("h24") or 0)
                    mcap = float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    quick_text = (
                        f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                        f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    if addr:
                        await message.reply_text(
                            quick_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}"
                                        )
                                    ],
                                    [web_scan_button(addr, chain)],
                                ]
                            ),
                            disable_web_page_preview=True,
                        )
                        return True
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
        TelegramError,
    ) as e:
        logger.debug("chat_ai $TOKEN lookup failed for %s: %s", symbol, e)
    return False


# ══════════════════════════════════════════════
# FEATURE ROUTING (free text only, NO AI quota)
# ══════════════════════════════════════════════


async def _route_structural(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, user: Any) -> bool:
    """Free-text feature routing that does NOT consume the AI quota.

    Ported from the old ``handlers/message.py handle_message`` so features
    survive the dedup: known-scam block, bare-address auto-scan, and the
    ``$TOKEN`` instant price card. Returns True when the message was fully
    handled (a reply was sent) and the caller should stop.

    The old natural-language-address-extraction block is NOT ported: it was
    dead code (EVM_RE/SOL_RE are whole-string anchored, so it could never
    fire after the bare-address check) and embedded addresses in questions
    are now handled better by :func:`chat_ai_response` address enhancement
    (risk summary prepended to the AI prompt).
    """
    message = update.message
    if message is None:
        return False

    # 1. Known scam addresses are blocked before any scan or AI call.
    #    Uses _extract_addresses (unanchored) so embedded scam addresses are
    #    caught too — strictly more protective than the old anchored check.
    for addr in _extract_addresses(text):
        if anti_abuse.is_known_scam_address(addr):
            logger.info("known-scam address blocked user=%s addr=%.16s", user.id, addr)
            await _scam_address_warning(update, addr)
            return True

    # 2. Bare contract address (the whole message is the address) → full /scan.
    if is_evm(text) or is_sol(text):
        ctx.args = [text]
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return True

    # 3. $TOKEN symbol lookup → instant DexScreener price card.
    return bool(await _lookup_token_symbol(text, message))


# ══════════════════════════════════════════════
# SHARED AI CORE (free text tail + /chat + /ask)
# ══════════════════════════════════════════════


async def _reply_local_intent(message: Message, text: str, user: Any) -> bool:
    """Tier-2 canned reply (no backend needed).

    Used for gibberish that is not worth an LLM call and as an offline
    fallback when the backend chat is unavailable. Returns True when a
    canned answer was sent.
    """
    local_response = local_ai_response(text, user)
    if not local_response:
        return False
    logger.info("AI chat tier=2 (local intent) user=%s text=%.60s", user.id, text)
    db.increment_usage(user.id, ai_msg=True)
    try:
        await track_ai_query()
        await track_tokens(len(local_response) // 4)
    except Exception:
        logger.debug("chat_ai analytics failed (user=%s)", user.id, exc_info=True)
    try:
        await message.reply_text(
            local_response,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai local intent reply failed", exc_info=True)
        return False
    return True


async def _reply_redirect(message: Message, user: Any, flood_receipt: str | None) -> None:
    """Tier-3 catch-all redirect (NOT counted against flood limits)."""
    logger.info("AI chat tier=3 (redirect) user=%s", user.id)
    try:
        await refund_flood(user.id, flood_receipt)
    except Exception:
        logger.debug("refund_flood failed (user=%s)", user.id, exc_info=True)
    db.increment_usage(user.id, ai_msg=True)
    try:
        await track_ai_query()
        await track_tokens(len(_REDIRECT_MESSAGE) // 4)
    except Exception:
        logger.debug("chat_ai analytics failed (user=%s)", user.id, exc_info=True)
    try:
        await message.reply_text(
            _REDIRECT_MESSAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(user.id),
            disable_web_page_preview=True,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai redirect reply failed", exc_info=True)


async def chat_ai_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, user: Any) -> bool:
    """Run the merged AI chat pipeline. Returns True when a reply was sent.

    Shared by the free-text tail of :func:`ai_chat_handler`, /chat
    (handlers/chat.py) and /ask (handlers/ask.py). Enforces the weekly
    AI-message quota per tier, per-user flood limits, and answers through
    the backend RAG chat (auto-RAG with news_v2).

    Same signature as the old ``handlers.message.ai_chat_response`` it
    replaces, so callers could be switched without rework.
    """
    message = update.message
    if message is None or not text.strip():
        return False

    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return True
    if check_spam(user.id):
        return True

    # AI chat can be toggled independently of the rest of the bot (admin).
    if db.get_bot_state("chat_enabled", "on") != "on":
        await message.reply_text(_CHAT_DISABLED, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id))
        return True

    # v6 anti-abuse: never forward prompt-injection attempts to the LLM.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked (chat_ai) user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return True

    # Weekly AI-message quota per tier (free 15 / scout 75 / hunter 250 / pro 500).
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        await message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return True

    # Per-user flood limit (3/s, 30/min) — same surface as the rest of AI chat.
    flood_ok, retry_after, flood_receipt = await check_flood(user.id)
    if not flood_ok:
        await message.reply_text(
            _SLOW_DOWN.format(retry=retry_after),
            parse_mode=ParseMode.HTML,
        )
        return True

    # Gibberish gate — don't burn LLM tokens on noise.
    if not _is_meaningful_question(text):
        await _reply_redirect(message, user, flood_receipt)
        return True

    # Tier-2: ONLY instant-reply for greetings, thanks, and capability inquiries.
    # Everything else goes to the LLM — the bot should answer what the user asks.
    if (_is_greeting_or_thanks(text) or _is_capability_question(text)) and await _reply_local_intent(
        message, text, user
    ):
        return True

    # Thumbs up — instant acknowledgment
    with contextlib.suppress(Exception):
        from telegram import ReactionTypeEmoji

        await message.set_reaction([ReactionTypeEmoji(emoji="👍")])

    # Start typing indicator — Telegram typing expires after ~5s, so we
    # keep it alive in a background task until the answer is ready.
    typing_task = asyncio.create_task(_keep_typing(message.chat))

    # Preliminary "thinking" message — keeps user engaged
    preliminary = await message.reply_text(
        "\U0001f916 <b>Thinking...</b>\n\n"
        f"\U0001f4ac <i>{escape(text[:150])}</i>\n\n"
        "\u23f1\ufe0f Working on a detailed answer...",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # CONCURRENT: fire the backend RAG chat (tier-1) AND the fallback context
    # fetches at the same time. The backend RAGs itself (news_v2 auto-RAG +
    # live web search) and does NOT need bridge context — only the Zen/local
    # fallback path does. Awaiting the backend result FIRST means the common
    # path never serializes on the ~12s bridge web search. The context tasks
    # are only awaited when the backend returns None and the fallback path
    # actually needs them (and are cancelled when the backend answers).
    addresses = _extract_addresses(text)
    history = cast("list[dict[str, str]]", db.get_chat_history(user.id))

    backend_task = asyncio.create_task(_call_backend_chat(text, user.id, history))
    daily_ctx_task = asyncio.create_task(get_daily_context())
    scan_ctx_task = asyncio.create_task(_fetch_scan_context(addresses[0])) if addresses else None
    bridge_ctx_task = asyncio.create_task(_fetch_bridge_context(text, history))

    # Tier-1: backend auto-RAG (POST /api/v1/chat, real X-RAG-Context count).
    # The backend injects the current date/time and runs its own RAG
    # (news_v2) + live web search, so it is already real-time aware.
    result = await backend_task

    # Bridge context/sources/actions only exist on the fallback path; the
    # backend answer carries its own grounding and never needs them.
    bridge_sources: list[dict[str, str]] = []
    bridge_actions: list[dict[str, str]] = []

    if result is None:
        # Fallback path: gather the context fetches (fail-open) that ground
        # the Zen/local LLM calls, then build the message-specific prompt.
        daily_context, scan_summary, bridge_ctx = await _gather_fallback_context(
            daily_ctx_task, scan_ctx_task, bridge_ctx_task
        )
        if bridge_ctx:
            bridge_context, bridge_sources, bridge_actions = bridge_ctx
        else:
            bridge_context = None

        # Fallback prompt: bridge blocks + scan summary (message-specific data).
        # The daily market/news digest is NOT glued to the user message — it is
        # passed as ``system_context`` so the fallback LLMs treat it as current
        # data in the SYSTEM prompt instead of part of the question (the
        # grounding rules already forbid quoting context verbatim).
        prompt = text
        if bridge_context:
            prompt = f"{bridge_context}\n\n{prompt}"
        if scan_summary:
            prompt = f"{scan_summary}\n\n{prompt}"

        # Fallback: cloud Zen, then local CPU llama.cpp — both grounded with
        # the bridge blocks, reporting rag_count = len(bridge sources).
        deep = _is_deep_question(text)
        result = await _call_zen_llm(
            prompt,
            user.id,
            history,
            deep=deep,
            sources=bridge_sources,
            system_context=daily_context,
        )
        if not result:
            result = await _call_local_llm(
                prompt,
                user.id,
                history,
                sources=bridge_sources,
                system_context=daily_context,
            )
    else:
        # Backend answered — the bridge/daily context fetches running in the
        # background are no longer needed. Cancel them so no coroutine dangles.
        pending = [t for t in (daily_ctx_task, scan_ctx_task, bridge_ctx_task) if t is not None]
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # Stop typing indicator
    typing_task.cancel()
    await asyncio.gather(typing_task, return_exceptions=True)

    if not result:
        if await _reply_local_intent(message, text, user):
            return True
        with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
            await preliminary.edit_text(
                _FALLBACK_MESSAGE,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        return True

    answer = result["message"]
    # Fallback answers were grounded by the bridge blocks — append the real
    # citation lines. Backend auto-RAG answers carry their own X-RAG-Context
    # count; citing bridge sources there would be dishonest.
    if result.get("provider") in ("zen", "local") and bridge_sources:
        answer = _append_source_lines(answer, bridge_sources)
    db.increment_usage(user.id, ai_msg=True)
    db.add_chat_message(user.id, "user", text)
    db.add_chat_message(user.id, "assistant", answer)
    with contextlib.suppress(Exception):
        await track_ai_query()
        await track_tokens(len(answer) // 4)

    # Dismiss the "Thinking…" placeholder, then send the answer as NEW
    # messages so the grounding footer + bridge action buttons render on the
    # last chunk (an edit cannot carry the footer/buttons reliably).
    with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
        await preliminary.edit_text("\u2705", parse_mode=ParseMode.HTML)
    await _send_answer(message, answer, result.get("rag_count", 0), actions=bridge_actions)
    return True


async def ai_chat_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Single free-text pipeline entry (MessageHandler).

    Merged from the old ``handlers.message.handle_message`` (owner admin
    console, bot kill-switch, known-scam block, bare-address auto-scan,
    $TOKEN price card) and the backend RAG chat core (:func:`chat_ai_response`).
    """
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.text:
        return
    text = message.text.strip()
    if not text:
        return

    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    if check_spam(user.id):
        return
    db.record_activity(msg=True)

    # Owner admin console (v7): in private chats the owner gets a persistent
    # admin reply keyboard + one-tap routing for its buttons. This runs
    # BEFORE the bot kill-switch so the owner can still reach /admin toggles
    # during maintenance.
    if is_owner(user.id) and update.effective_chat and update.effective_chat.type == "private":
        await _ensure_admin_reply_kb(update)
        if text in _ADMIN_BUTTON_MAP:
            await _dispatch_admin_button(update, ctx, text)
            return

    # Admin kill-switch: whole bot disabled (synced from the admin API).
    if db.get_bot_state("bot_enabled", "on") != "on":
        return

    # Analytics: count every inbound message handled (Redis, for the admin API).
    try:
        await track_message()
    except Exception:
        logger.debug("admin_state track_message failed", exc_info=True)

    # v6 anti-abuse: prompt injection + known scam addresses. Injection is
    # blocked BEFORE feature routing so an injected address is never scanned.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return

    # Feature routing that does NOT consume the AI quota: known-scam block,
    # bare-address auto-scan, $TOKEN instant price card.
    if await _route_structural(update, ctx, text, user):
        return

    # Free text → shared AI core (quota + flood + backend RAG chat).
    await chat_ai_response(update, ctx, text, user)


async def _send_answer(
    message: Message,
    answer: str,
    rag_count: int,
    *,
    actions: list[dict[str, str]] | None = None,
) -> None:
    """Reply with the answer, chunked to Telegram's size limit.

    v10 pipeline (all on the RAW text first, then escape):
      1. ``_beautify_answer`` normalizes emoji section headers and inserts
         thin separators beneath them.
      2. ``_chunk_text`` splits at paragraph / section-header boundaries —
         never mid-sentence when a sentence boundary exists.
      3. Each chunk is HTML-escaped (LLM output is not trusted HTML).
      4. ``_markdown_bold_to_html`` converts the **bold** metrics the persona
         emits into real ``<b>...</b>`` — safe because the content was
         already escaped in step 3.
    A short "⚡ Grounded in N intelligence sources | RugMunch Bot" footer is
    appended to the last chunk when the backend reported RAG grounding.
    ``rag_count`` is the REAL number of unique RAG hits (from
    ``X-RAG-Context``, or ``len(bridge sources)`` on the fallback path);
    counts above 100 are shown as "hundreds of sources".
    ``actions`` (bridge-suggested buttons) are rendered as an inline
    keyboard on the last chunk when present; otherwise the back menu is used.
    """
    raw = _beautify_answer(answer)
    chunks = _chunk_text(raw)
    if not chunks:
        return
    escaped = [escape(c) for c in chunks]
    html = [_markdown_bold_to_html(c) for c in escaped]
    if rag_count > 0:
        if rag_count > 100:
            source_label = "hundreds of intelligence sources"
        elif rag_count == 1:
            source_label = "1 intelligence source"
        else:
            source_label = f"{rag_count} intelligence sources"
        html[-1] = f"{html[-1]}\n\n<i>\u26a1 Grounded in {source_label} | RugMunch Bot</i>"

    kb = _render_bridge_actions(actions) or back_kb()
    last = len(html) - 1
    for i, chunk in enumerate(html):
        # Final safety clip: never exceed Telegram's hard 4096-char limit even
        # when entity expansion pushed a chunk over.
        if len(chunk) > 4000:
            chunk = chunk[:4000]
        try:
            await message.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                reply_markup=kb if i == last else None,
                disable_web_page_preview=True,
            )
        except (TelegramError, ConnectionError, OSError, TimeoutError) as e:
            logger.debug("chat_ai reply failed chunk=%d: %s", i, e)
            # Degrade: send without parse mode so the user still gets the text.
            try:
                await message.reply_text(chunk)
            except (TelegramError, ConnectionError, OSError, TimeoutError):
                return


def register_chat_ai(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the free-text pipeline (MessageHandler) on the bot Application.

    One-line wiring for bot.py — this is the ONLY free-text handler:

        from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
        register_chat_ai(app)

    WIRING NOTE: python-telegram-bot dispatches to the FIRST matching handler,
    so no other ``MessageHandler(filters.TEXT & ~filters.COMMAND)`` may be
    registered alongside this one (the old ``handle_message`` registration
    was removed when the pipelines were merged).
    """
    from telegram.ext import MessageHandler, filters

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))


__all__ = [
    "CHAT_ENDPOINT",
    "CHAT_TIMEOUT",
    "CHUNK_MAX_CHARS",
    "MAX_ADDRESS_HINTS",
    "SCAN_ENDPOINT",
    "SCAN_TIMEOUT",
    "_is_section_header",
    "_split_oversized",
    "ai_chat_handler",
    "chat_ai_response",
    "local_ai_response",
    "register_chat_ai",
    "thin_sep",
]
