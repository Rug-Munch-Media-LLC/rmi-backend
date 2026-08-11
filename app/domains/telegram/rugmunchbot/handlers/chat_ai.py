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
  * Backend ``POST /api/v1/chat`` call (auto-RAG with ``news_v2`` grounding,
    per-user history via the bot SQLite DB, per-user rate limiting via
    ``X-Forwarded-For``)
  * Address-in-question enhancement (scan risk summary prepended to the
    prompt, best-effort)
  * Gibberish gate, weekly quota, flood control, chunked HTML-safe answers

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

from app.core.logging import get_logger
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

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — LOCAL MODELS
# ══════════════════════════════════════════════
# Qwen 3 4B for quick chat (fast, conversational)
# Bridge gateway — containers can't reach host 127.0.0.1
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")
QWEN_CHAT_URL = f"http://{LLAMA_HOST}:8085/v1/chat/completions"
QWEN_MODEL = "qwen-chat"
QWEN_TIMEOUT = 60.0

# DeepSeek R1 Distill Qwen 8B for deep analysis (hides thinking output)
DEEPSEEK_CHAT_URL = f"http://{LLAMA_HOST}:8086/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-analysis"
DEEPSEEK_TIMEOUT = 120.0

# Backend RAG chat (when available, for RAG-grounded answers)
CHAT_ENDPOINT = f"{BACKEND_URL}/api/v1/chat"
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"
CHAT_TIMEOUT = 60.0
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit
CHUNK_MAX_CHARS = 3800  # Telegram hard limit is 4096; leave headroom for escape expansion
MAX_ADDRESS_HINTS = 2  # cap address-triggered scans per message

# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER (see _beautify_answer / _markdown_bold_to_html)
# Section emojis match the persona's RESPONSE FORMATTING block (prompts.py).
# A line that STARTS with one of these and is short is treated as a section
# header: normalized to **emoji Title**, with thin_sep() inserted beneath it.
# ══════════════════════════════════════════════
_SECTION_EMOJIS: frozenset[str] = frozenset(
    [
        "\U0001f4ca",  # 📊 statistics
        "\U0001f4c8",  # 📈 chart increasing
        "\U0001f4c9",  # 📉 chart decreasing
        "\U0001f50d",  # 🔍 magnifying glass
        "\U0001f6e1\ufe0f",  # 🛡️ shield
        "\U0001f40b",  # 🐋 whale
        "\U0001f464",  # 👤 bust
        "\U0001f4b0",  # 💰 money bag
        "\U0001f525",  # 🔥 fire
        "\U0001f6a8",  # 🚨 siren
        "\u2705",  # ✅ check
        "\u26a0\ufe0f",  # ⚠️ warning
        "\U0001f4ac",  # 💬 speech balloon
        "\U0001f4f0",  # 📰 newspaper
        "\U0001f465",  # 👥 people
        "\U0001f680",  # 🚀 rocket
    ]
)
_SECTION_HEADER_MAX = 80  # longer emoji-prefixed lines are content, not headers
_BOLD_RE = re.compile(r"\*{2,3}([^*\n]+?)\*{2,3}")

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
        admin_kb_reset(update.effective_chat.id)
        await cmd_start(update, ctx)


# ══════════════════════════════════════════════
# ADDRESS DETECTION (free-text search, mirrors app/routers/chat.py)
# ══════════════════════════════════════════════
_EVM_ADDR_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_ADDR_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def _plausible_solana(addr: str) -> bool:
    """Base58 address-likeness: real Solana addresses almost always mix digits
    or uppercase with lowercase, so this filters word-shaped false positives
    ("supercalifragilisticexpialidocious") without dropping genuine tokens."""
    return any(ch.isdigit() or ch.isupper() for ch in addr)


def _extract_addresses(text: str) -> list[str]:
    """Return token addresses (EVM 0x... / Solana base58) found in free text.

    Capped at ``MAX_ADDRESS_HINTS`` so a single message cannot trigger a scan
    storm. Solana matches that overlap an EVM match are skipped.
    """
    addresses: list[str] = []
    for m in _EVM_ADDR_RE.finditer(text):
        addresses.append(m.group(0))
        if len(addresses) >= MAX_ADDRESS_HINTS:
            return addresses
    for m in _SOL_ADDR_RE.finditer(text):
        addr = m.group(0)
        if addr not in addresses and _plausible_solana(addr):
            addresses.append(addr)
        if len(addresses) >= MAX_ADDRESS_HINTS:
            break
    return addresses


# ══════════════════════════════════════════════
# SCANNER CONTEXT (address questions get a risk summary prepended)
# ══════════════════════════════════════════════


def _format_scan_summary(address: str, data: dict[str, Any]) -> str | None:
    """Compact single-line risk summary used to ground an address question.

    Returns ``None`` when the scanner returned no risk score so the caller
    falls through to a plain question.
    """
    risk = data.get("risk_score")
    if risk is None:
        return None
    flags = [f"risk_score={int(risk)}"]
    for key in ("is_honeypot", "is_mintable", "is_renounced"):
        val = data.get(key)
        if val is not None:
            flags.append(f"{key}={bool(val)}")
    liq = data.get("liquidity_usd") or 0
    if liq:
        flags.append(f"liquidity=${float(liq):,.0f}")
    holders = data.get("holders")
    if holders:
        flags.append(f"holders={holders}")
    return f"Context: token {address} scan shows {', '.join(flags)}"


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


# ══════════════════════════════════════════════
# BACKEND CHAT CALL
# ══════════════════════════════════════════════


async def _call_backend_chat(
    message: str,
    user_id: int,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Chat with local models (Qwen 3 4B for quick, DeepSeek 8B for analysis).

    Uses llama.cpp servers running locally. Qwen for fast chat, DeepSeek
    for deep analysis (thinking output is hidden from the user).

    Returns a dict with ``message``, ``rag_count``, ``tokens``, ``model``,
    ``provider`` and ``latency_ms``, or ``None`` on any failure/timeout.
    """
    # Determine mode based on message complexity
    is_deep = any(
        kw in message.lower()
        for kw in [
            "analyze",
            "analysis",
            "deep",
            "explain",
            "why",
            "how does",
            "compare",
            "risk",
            "detail",
            "technical",
            "report",
        ]
    )

    if is_deep:
        url = DEEPSEEK_CHAT_URL
        model = DEEPSEEK_MODEL
        timeout = DEEPSEEK_TIMEOUT
    else:
        url = QWEN_CHAT_URL
        model = QWEN_MODEL
        timeout = QWEN_TIMEOUT

    # Build messages with conversation history
    messages = [
        {
            "role": "system",
            "content": (
                "You are Rug Munch, an AI crypto intelligence assistant. "
                "You help users understand crypto security, detect scams, analyze tokens, "
                "and answer questions about blockchain, DeFi, and crypto markets. "
                "Be direct, witty, slightly cynical but genuinely helpful. "
                "Never give financial advice. Keep answers concise (2-4 sentences). "
                "If you don't know something, say so honestly. "
                "IMPORTANT: Do not output any thinking or reasoning. Just give the final answer directly."
            ),
        }
    ]
    if history:
        for msg in history[-5:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message[:2000]})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.7,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("local chat unavailable (user=%s): %s", user_id, e)
        return None
    if r.status_code != 200:
        logger.debug("local chat HTTP %d (user=%s)", r.status_code, user_id)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    choices = raw.get("choices", [])
    if not choices or not isinstance(choices, list):
        return None
    answer = (choices[0].get("message", {}) or {}).get("content", "").strip()

    # Strip thinking output from DeepSeek and Qwen.
    # Handle BOTH closed <think>...</think> AND unclosed <think> (token budget
    # exhausted before the model finishes reasoning).
    import re

    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
    answer = re.sub(r"<think>.*", "", answer, flags=re.DOTALL)
    answer = re.sub(r"</?answer>", "", answer)
    answer = re.sub(r"<think>", "", answer)
    answer = re.sub(r"</think>", "", answer)
    # Remove thinking text (models often think out loud before giving the answer)
    # Take only the last substantive paragraph as the actual answer
    paragraphs = [p.strip() for p in answer.split("\n") if p.strip()]
    if len(paragraphs) > 1:
        # Take the last paragraph that doesn't start with thinking patterns
        for p in reversed(paragraphs):
            if not any(
                p.lower().startswith(prefix)
                for prefix in [
                    "okay",
                    "let me",
                    "first",
                    "i need",
                    "i should",
                    "i will",
                    "i can",
                    "so i",
                    "well",
                    "hmm",
                    "the user",
                    "the question",
                    "i think",
                    "i believe",
                    "i understand",
                    "i need to",
                    "so the",
                    "the answer",
                    "the user asked",
                    "since they're",
                    "since the",
                ]
            ):
                answer = p
                break
    answer = answer.strip()

    if not answer:
        return None

    return {
        "message": answer,
        "rag_count": 0,
        "tokens": raw.get("usage", {}).get("total_tokens", 0),
        "model": model,
        "provider": "local",
        "latency_ms": 0,
    }


# ══════════════════════════════════════════════
# LOCAL LLM FALLBACK (free chatting when backend is down)
# ══════════════════════════════════════════════


async def _call_local_llm(
    message: str,
    user_id: int,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Local LLM fallback via llama.cpp servers for free chatting.

    Qwen 3 4B for quick chat, DeepSeek R1 Distill Qwen 8B for deep analysis.
    Thinking output is stripped from responses.
    """
    # Build messages array with conversation history
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are Rug Munch, a crypto AI assistant. "
                "Be direct and concise. Never give financial advice. "
                "Do not output thinking or reasoning. Just give the final answer directly."
            ),
        }
    ]
    # Add conversation history (last 3 messages — less context = faster)
    if history:
        for msg in history[-3:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:500]})
    # Add current message
    messages.append({"role": "user", "content": message[:2000]})

    # Try each available model in order (Qwen first for speed, DeepSeek for depth).
    # chat_template_kwargs.enable_thinking=false disables the Qwen3/DeepSeek
    # reasoning preamble so all tokens go to the actual answer. Without this the
    # model burns the entire budget on <think> blocks and returns nothing.
    for url, model, timeout in [
        (QWEN_CHAT_URL, QWEN_MODEL, QWEN_TIMEOUT),
        (DEEPSEEK_CHAT_URL, DEEPSEEK_MODEL, DEEPSEEK_TIMEOUT),
    ]:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=10.0)
            ) as client:
                r = await client.post(
                    url,
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 512,
                        "temperature": 0.7,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
            logger.debug("local LLM %s unavailable (user=%s): %s", model, user_id, e)
            continue
        if r.status_code != 200:
            logger.debug("local LLM %s HTTP %d (user=%s)", model, r.status_code, user_id)
            continue
        try:
            raw = r.json()
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        choices = raw.get("choices", [])
        if not choices or not isinstance(choices, list):
            continue
        answer = (choices[0].get("message", {}) or {}).get("content", "").strip()

        # Strip thinking output (DeepSeek and Qwen both emit <think> blocks).
        # Handle BOTH closed <think>...</think> AND unclosed <think> (when the
        # token budget runs out before the model finishes reasoning).
        import re

        # First remove properly closed think blocks
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
        # Then remove any remaining unclosed <think> tag and everything after it
        answer = re.sub(r"<think>.*", "", answer, flags=re.DOTALL)
        answer = re.sub(r"</?answer>", "", answer)
        answer = re.sub(r"<think>", "", answer)
        answer = re.sub(r"</think>", "", answer)
        # Take only the last substantive paragraph (thinking usually comes first)
        paragraphs = [p.strip() for p in answer.split("\n") if p.strip()]
        if len(paragraphs) > 1:
            for p in reversed(paragraphs):
                if not any(
                    p.lower().startswith(prefix)
                    for prefix in [
                        "okay",
                        "let me",
                        "first",
                        "i need",
                        "i should",
                        "i will",
                        "i can",
                        "so i",
                        "well",
                        "hmm",
                        "the user",
                        "the question",
                        "i think",
                        "i believe",
                        "i understand",
                        "i need to",
                        "so the",
                        "the answer",
                        "the user asked",
                        "since they're",
                        "since the",
                        "so that's",
                        "i'll",
                        "let's",
                        "looking at",
                    ]
                ):
                    answer = p
                    break
        answer = answer.strip()
        if not answer:
            continue
        return {
            "message": answer,
            "rag_count": 0,
            "tokens": raw.get("usage", {}).get("total_tokens", 0),
            "model": model,
            "provider": "local",
            "latency_ms": 0,
        }
    return None


def _last_sentence_boundary(text: str, limit: int) -> int:
    """Index just past the last sentence-ending punctuation before ``limit``.

    Returns -1 when no sentence boundary exists inside the window. Used by
    :func:`_split_oversized` so hard splits land at sentence ends instead of
    mid-sentence ("split at natural section boundaries, not mid-sentence").
    """
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    if best > limit:
        return limit  # boundary punctuation ends exactly at the window edge
    return best


def _split_oversized(paragraph: str, max_len: int) -> list[str]:
    """Hard-split a single oversized paragraph — prefer sentence boundaries.

    Falls back to the last word boundary, then an exact cut, so a cut never
    lands mid-word. Sentence-boundary preference keeps chunks readable.
    """
    if len(paragraph) <= max_len:
        return [paragraph]
    parts: list[str] = []
    remaining = paragraph
    while len(remaining) > max_len:
        cut = _last_sentence_boundary(remaining, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind(" ", 0, max_len)  # no sentence end — word boundary
        if cut < max_len // 2:
            cut = max_len  # no good boundary — cut exactly
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_text(text: str, max_len: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized chunks on paragraph boundaries.

    Operates on the RAW (pre-escape) text: paragraphs are the safest split
    points and no chunk ever cuts through an HTML entity that escaping would
    later introduce (e.g. ``&lt;``). New emoji section headers (v10) always
    start a fresh chunk when the current one is non-empty, so headers never
    dangle at the tail of a previous section.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        for piece in _split_oversized(paragraph, max_len):
            if current and _is_section_header(piece):
                # v10: prefer section boundaries — a new emoji header leads a
                # new chunk so it is never glued to the previous section.
                chunks.append(current)
                current = ""
            elif current and len(current) + len(piece) + 2 > max_len:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER
# Runs on the RAW LLM answer BEFORE chunking/escaping: normalizes emoji
# section headers, inserts thin separators under them, and keeps markdown
# **bold** markers for the post-escape conversion in _send_answer.
# ══════════════════════════════════════════════


def _is_section_header(line: str) -> bool:
    """True for a short line that begins with a section emoji (v10 header).

    A line starting with an emoji but longer than ``_SECTION_HEADER_MAX`` is
    prose, not a header — e.g. "📊 This token's chart broke out of a wedge
    that's been building since March" stays content. Prefix matching handles
    multi-code-point emojis (🛡️ = U+1F6E1 + VS16).
    """
    core = line.strip().lstrip("*").strip()
    if not core or len(core) > _SECTION_HEADER_MAX:
        return False
    return any(core.startswith(emoji) for emoji in _SECTION_EMOJIS)


def _unwrap_bold(line: str) -> str:
    """Strip a balanced ``**...**``/``***...***`` wrapper (or leading `*`)."""
    stripped = line.strip()
    m = re.match(r"^\*{2,3}(.+?)\*{2,3}$", stripped)
    if m:
        return m.group(1).strip()
    return re.sub(r"^\*{1,3}", "", stripped).strip()


def _beautify_answer(answer: str) -> str:
    """v10 beautifier — normalize headers + insert thin separators (raw text).

    - Lines that look like section headers (start with a section emoji,
      short) are normalized to ``**emoji Title**`` on their own line, with a
      blank line before them so the chunker sees a clean section boundary.
    - ``thin_sep()`` is inserted beneath every header for visual rhythm
      (📊 header / ──── / content, like the /scan report blocks).
    - All other lines are preserved verbatim (code blocks, lists, spacing).
    Markdown ``**bold**`` is left in place for :func:`_markdown_bold_to_html`
    which runs AFTER escaping in :func:`_send_answer`.
    """
    lines = answer.split("\n")
    out: list[str] = []
    prev_empty = True
    for raw_line in lines:
        if not raw_line.strip():
            out.append("")
            prev_empty = True
            continue
        if _is_section_header(raw_line):
            if not prev_empty:
                out.append("")
            out.append(f"**{_unwrap_bold(raw_line)}**")
            out.append(thin_sep())
            prev_empty = False
            continue
        out.append(raw_line)
        prev_empty = False
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _markdown_bold_to_html(text: str) -> str:
    """Convert ``**bold**`` / ``***bold***`` to ``<b>bold</b>``.

    MUST run AFTER ``html.escape()`` so the wrapped content is already
    entity-safe and only the asterisk pairs are replaced — LLM-supplied HTML
    is never re-introduced. Unbalanced asterisks pass through untouched.
    """
    return _BOLD_RE.sub(r"<b>\1</b>", text)


# ══════════════════════════════════════════════
# GIBBERISH GATE (was handlers/message.py tier-1b gate)
# ══════════════════════════════════════════════


def _is_meaningful_question(text: str) -> bool:
    """Return False for gibberish / one-liners not worth an LLM call."""
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    words = re.findall(r"[A-Za-z]{3,}", stripped)
    if not words:
        return False
    if len(stripped) > 0 and sum(1 for ch in stripped if ch.isalpha()) / len(stripped) < 0.35:
        return False
    # Repeated single word / character spam: "asdfasdfasdf", "lllll"
    return not (len(set(words)) == 1 and len(words) > 4)


# ══════════════════════════════════════════════
# ANTI-ABUSE REPLIES (ported from handlers/message.py)
# ══════════════════════════════════════════════

_INJECTION_MESSAGES: dict[str, str] = {
    "instruction-override": (
        "I'm designed to follow my original security instructions. I can't be re-prompted to ignore them."
    ),
    "secret-extraction": ("I don't reveal system prompts, API keys or internal configuration."),
    "role-switch": ("I'm RugMunch's assistant — I don't switch to developer/uncensored modes."),
    "echo-poisoning": ("I don't echo raw prompts or messages — ask me a normal question instead."),
    "wallet-secret-harvest": ("Never share your seed phrase or private keys with anyone — including bots."),
    "wallet-drain-social": (
        "Be careful! Legitimate airdrops never ask you to connect your wallet or approve transactions to claim."
    ),
    "forced-promotion": ("I don't promote tokens or post messages on request."),
}

_KNOWN_SCAM_WARNING = (
    "\U0001f6a8 <b>Warning: Known Scam Address</b>\n\n"
    "The address you shared is on our <b>known scam denylist</b> — it has "
    "been reported by other users and flagged for suspicious activity.\n\n"
    "\U0001f4a1 <b>Do not interact with this token.</b>\n"
    "\u2022 Don't approve any transactions\n"
    "\u2022 Don't connect your wallet to its site\n"
    "\u2022 If you've bought it, watch for exit liquidity pulls\n\n"
    "Thanks for checking with RugMunch first!"
)


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
        await update.message.reply_text(
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
        await update.message.reply_text(
            f"{_KNOWN_SCAM_WARNING}\n\n"
            f"\U0001f517 Address: <code>{address}</code>\n\n"
            f"Already flagged one like this? /report helps us build the denylist.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("scam address warning send failed", exc_info=True)


# ══════════════════════════════════════════════
# TIER-2 LOCAL INTENT MATCHING (ported from handlers/message.py)
# Canned, zero-backend answers for greetings / feature intents. Used for
# gibberish that is not worth an LLM call and as an offline fallback when the
# backend chat is unavailable.
# ══════════════════════════════════════════════

_INTENT_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"\b(scan|check|audit|rug|scam|honeypot|safu|drain|phish|malicious|"
            r"dangerous|secure|risky|safe(ty)?|is this safe|is it safe|trustworthy)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f50d <b>Token Security Scan</b>\n\n"
            "Use <code>/scan &lt;address&gt;</code> to run a full security audit. "
            "I check for:\n"
            "\u2022 \U0001f36f Honeypot detection\n"
            "\u2022 \U0001f3c3 Rug pull risks\n"
            "\u2022 \U0001f4e6 Bundle / wash trading\n"
            "\u2022 \U0001f9db Dev wallet history\n"
            "\u2022 \U0001f4ca Liquidity & holder analysis\n\n"
            "Just paste any contract address and I'll auto-detect it!"
        ),
    ),
    (
        re.compile(
            r"\b(price|chart|worth|value|how much|cost|mc[aap]+|market cap|volume|"
            r"liquidity|pump|dump|buy|sell|token price|usd)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f4ca <b>Price & Market Data</b>\n\n"
            "Use <code>/quick &lt;symbol&gt;</code> or <code>$TOKEN</code> for instant prices.\n"
            "Use <code>/chart &lt;address&gt;</code> for technical analysis charts.\n"
            "Use <code>/scan &lt;address&gt;</code> for full market data.\n\n"
            "\U0001f4b0 Real-time market data for any token."
        ),
    ),
    (
        re.compile(
            r"\b(wallet|portfolio|balance|holdings|my token|my coin|bag|"
            r"hodl|fund|money)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f9db <b>Wallet Analysis</b>\n\n"
            "Use <code>/wallet &lt;address&gt;</code> to analyze any wallet.\n"
            "Use <code>/portfolio</code> to view your tracked wallets.\n\n"
            "I show:\n"
            "\u2022 \U0001f4b0 Token balances & values\n"
            "\u2022 \U0001f504 Recent transaction history\n"
            "\u2022 \u26a0\ufe0f Risk metrics & scam checks\n"
            "\u2022 \U0001f517 Counterparty connections"
        ),
    ),
    (
        re.compile(r"\b(compare|vs |versus|better than|which (is|one)|difference)\b", re.IGNORECASE),
        (
            "\u2696\ufe0f <b>Token Comparison</b>\n\n"
            "Use <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code> to pit two tokens "
            "side-by-side.\n\n"
            "I'll show you both risk scores, prices, liquidity, and holder metrics "
            "in one view!"
        ),
    ),
    (
        re.compile(
            r"\b(trending|hot|popular|what to buy|best coin|moon|gem|"
            r"next 100x|top token|gainers|movers)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Trending Tokens</b>\n\n"
            "Check <code>/trending</code> for the hottest tokens right now.\n"
            "I show real-time data on:\n"
            "\u2022 \U0001f4c8 Top gainers\n"
            "\u2022 \U0001f4a7 Highest volume\n"
            "\u2022 \U0001f195 New pairs\n"
            "\u2022 \U0001f3e0 Most holders"
        ),
    ),
    (
        re.compile(
            r"\b(alert|notify|watch|track|monitor|notification|remind|follow)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "Use <code>/watch &lt;address&gt;</code> to add tokens to your watchlist.\n"
            "Use <code>/alerts</code> to manage your price alerts.\n\n"
            "\U0001f4cc <b>Scout+</b> tier gets real-time push alerts for price moves, "
            "large transactions, and security events."
        ),
    ),
    (
        re.compile(
            r"\b(mev|sandwich|frontrun|backrun|protect(ion)? (trade|swap)|"
            r"safe swap|slippage|gas optimal|mev blocker|flashbot)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f6e1\ufe0f <b>MEV Protection</b>\n\n"
            "Use <code>/mev &lt;token_address&gt;</code> to check sandwich attack risk "
            "and get safe swap parameters:\n"
            "• Frontrunning risk score\n"
            "• Recommended slippage tolerance\n"
            "• Optimal gas settings\n"
            "• MEV-blocker routing suggestion\n\n"
            "<i>Free during beta — protect your trades from MEV bots.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(impersonat|fake token|copycat|clone token|pretending to be|"
            r"fake version|counterfeit|fraudulent token)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f97e <b>Impersonation Detector</b>\n\n"
            "Use <code>/impersonation &lt;token_address&gt;</code> to audit for:\n"
            "• Name/symbol similarity to known tokens\n"
            "• Honeypot delegates & hidden permissions\n"
            "• Related malicious contracts\n\n"
            "<i>Free during beta — spot fake tokens before you buy.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(death clock|how long (will|does)|survival|when (will|does) (it|token) die|"
            r"rug countdown|abandonment|token lifespan|longevity)\b",
            re.IGNORECASE,
        ),
        (
            "\u23f0 <b>Death Clock</b>\n\n"
            "Use <code>/deathclock &lt;chain&gt; &lt;token_address&gt;</code> to predict:\n"
            "• Days until likely death (rug or abandonment)\n"
            "• Key risk factors\n"
            "• Survival probability\n\n"
            "<i>Free during beta — know the risk before you hold.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(new launch|just launched|fresh token|new token|recent launch|"
            "brand new|just dropped|newly created)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f525 <b>Token Launches</b>\n\n"
            "Use <code>/launches [chain]</code> to see new token launches with:\n"
            "• Auto-risk flags (low/medium/high)\n"
            "• Liquidity & holder counts\n"
            "• Direct scan buttons\n\n"
            "<i>Free during beta — discover new tokens early.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(cluster|related wallet|funding path|same owner|connected wallet|"
            r"wallet group|bundle|coordinated|sniper bot|deployer funded)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f578\ufe0f <b>Wallet Clustering</b>\n\n"
            "Use <code>/cluster &lt;wallet_address&gt;</code> to detect:\n"
            "• Related wallets by behavior/funding\n"
            "• Fund origin (CEX, mixer, other tokens)\n"
            "• Deployer-funded wallets (bundle detection)\n\n"
            "<i>Free during beta — uncover hidden wallet connections.</i>"
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
            r"gm|gn|what'?s up|welcome|are you (there|alive|up|online|working)|"
            r"you (there|alive|up|online|working)|ping|wake up|respond)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f44b <b>Yo! I'm here.</b>\n\n"
            "I'm <b>RugMunchBot</b> \u2014 the AI analyst from the <b>Rug Munch Intelligence</b> platform. "
            "I scan tokens for rugs, profile wallets, track whales, and "
            "answer crypto questions backed by 91K+ intel documents across 25+ chains.\n\n"
            "\U0001f6e1\ufe0f <b>Quick start:</b>\n"
            "\u2022 Paste a contract address \u2192 instant scam scan\n"
            "\u2022 <code>/scan &lt;addr&gt;</code> \u2192 deep security audit\n"
            "\u2022 <code>/wallet &lt;addr&gt;</code> \u2192 wallet forensics\n"
            "\u2022 <code>/market</code> \u2192 live market overview\n"
            "\u2022 <code>$TOKEN</code> \u2192 price lookup\n"
            "\u2022 <code>/mev</code> \u2192 MEV risk & safe swap params\n"
            "\u2022 <code>/deathclock</code> \u2192 token survival prediction\n"
            "\u2022 <code>/help</code> \u2192 full command list\n\n"
            "Or just ask me anything about crypto \u2014 scams, hacks, market moves, "
            "regulations, whatever's on your mind."
            f"{footer_links()}"
        ),
    ),
    (
        re.compile(r"\b(thank|thanks|ty|thx|appreciate|good bot|nice|great)\b", re.IGNORECASE),
        (
            "\U0001f64c You're welcome! Happy to help keep the crypto space safe.\n\n"
            "If you find this useful, tell your friends! \n"
            "Use <code>/refer</code> to get your referral link."
        ),
    ),
    (
        re.compile(
            r"\b(help|can you|capabilities|features|commands|what do|what are|"
            r"who are you|tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f916 <b>RugMunch Capabilities</b>\n\n"
            "I'm a multi-chain crypto intelligence bot. Here's what I can do:\n\n"
            "\U0001f50d <code>/scan &lt;addr&gt;</code> \u2014 Full token security audit\n"
            "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 Wallet forensics\n"
            "\U0001f4ca <code>/quick &lt;symbol&gt;</code> \u2014 Price lookup\n"
            "\U0001f4c8 <code>/chart &lt;addr&gt;</code> \u2014 Technical analysis\n"
            "\u2696\ufe0f <code>/compare &lt;a&gt; &lt;b&gt;</code> \u2014 Side-by-side compare\n"
            "\U0001f525 <code>/trending</code> \u2014 Hot tokens by chain\n"
            "\U0001f4f0 <code>/news</code> \u2014 Crypto headlines\n"
            "\U0001f393 <code>/scamschool</code> \u2014 Learn scam patterns\n"
            "\U0001f6e1\ufe0f <code>/mev</code> \u2014 MEV risk & safe swap params\n"
            "\U0001f97e <code>/impersonation</code> \u2014 Detect fake tokens\n"
            "\u23f0 <code>/deathclock</code> \u2014 Token longevity prediction\n"
            "\U0001f525 <code>/launches</code> \u2014 New token launch feed\n"
            "\U0001f578\ufe0f <code>/cluster</code> \u2014 Wallet clustering & funding\n"
            "\U0001f451 <code>/pricing</code> \u2014 Upgrade your tier"
            f"{footer_links()}"
        ),
    ),
]


def _is_greeting_or_thanks(text: str) -> bool:
    """Check if the message is a greeting or thanks (canned reply worthy)."""
    return bool(
        re.search(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|gm|gn|"
            r"what'?s up|ping|wake up|"
            r"thank|thanks|ty|thx|appreciate|good bot|nice|great)\b",
            text.lower(),
        )
    )


def _is_capability_question(text: str) -> bool:
    """Check if the message asks about bot capabilities (canned reply worthy)."""
    return bool(
        re.search(
            r"\b(help|can you|capabilities|features|commands|what do|what are|"
            r"who are you|tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            text.lower(),
        )
    )


def local_ai_response(text: str, user: Any) -> str | None:
    """Tier 2: Match user intent against keyword patterns."""
    text_lower = text.lower().strip()
    for pattern, response in _INTENT_RULES:
        if pattern.search(text_lower):
            if len(text_lower) < 2:
                return None
            return response
    return None


# ══════════════════════════════════════════════
# TIER-3 FRIENDLY REDIRECT (ported from handlers/message.py)
# ══════════════════════════════════════════════

_REDIRECT_MESSAGE = (
    "\U0001f916 <b>RugMunch AI</b>\n\n"
    "I can help with token scans, wallet analysis, market data, DeFi, "
    "crypto news and even coding questions. Try:\n\n"
    "\U0001f4ac <code>/chat &lt;question&gt;</code> \u2014 talk to me about anything\n"
    "\U0001f50d <code>/scan &lt;address&gt;</code> \u2014 security audit\n"
    "\U0001f4cc <code>$TOKEN</code> \u2014 instant price lookup\n"
    "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 wallet analysis\n"
    "\U0001f525 <code>/trending</code> \u2014 hot tokens by chain\n"
    "\U0001f6e1\ufe0f <code>/mev</code> \u2014 MEV protection\n"
    "\U0001f97e <code>/impersonation</code> \u2014 detect fake tokens\n"
    "\u23f0 <code>/deathclock</code> \u2014 token survival prediction\n"
    "\U0001f4da <code>/scamschool</code> \u2014 learn scam patterns"
    f"{footer_links()}"
)


# ══════════════════════════════════════════════
# HANDLER MESSAGES
# ══════════════════════════════════════════════

_FALLBACK_MESSAGE = (
    "\U0001f9e0 <b>AI brain temporarily unavailable</b>\n\n"
    "I couldn't reach the intelligence engine right now. Use "
    "<code>/scan &lt;address&gt;</code> for direct analysis, or try again "
    "in a moment."
)

_SLOW_DOWN = "\U0001f422 <b>Slow down!</b>\n\nYou're sending messages too fast. Please wait {retry}s and try again."

_CHAT_DISABLED = (
    "\U0001f4ac <b>AI Chat is temporarily disabled</b>\n\n"
    "The chat feature is under maintenance. Commands like /scan, /wallet "
    "and /price still work \u2014 just send a contract address!"
)

_THINKING_GENERAL = (
    "\U0001f916 <b>On it...</b>\n\n\U0001f4ac <i>{question}</i>\n\n\U0001f50d Checking our intelligence database..."
)

_THINKING_ADDRESS = (
    "\U0001f916 <b>On it...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f575\ufe0f Running token scan \u2192 checking exploit history \u2192 pulling live market data..."
)

_THINKING_INVESTIGATE = (
    "\U0001f916 <b>Deep diving...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4da Cross-referencing research collections + scanner data + news feeds..."
)

_THINKING_PRICE = (
    "\U0001f916 <b>Looking that up...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4b0 Fetching live prices + market data..."
)

_THINKING_NEWS = (
    "\U0001f916 <b>Checking the wire...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f4f0 Pulling latest crypto news + market intelligence..."
)

_THINKING_SCAM = (
    "\U0001f916 <b>Checking the threat board...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\U0001f6e1\ufe0f Searching scam databases + exploit patterns + known rug pulls..."
)

# "Investigate-style" intents get the research-collections placeholder instead
# of the generic DB-search one (matches the /investigate deep-dive framing).
_INVESTIGATE_INTENT_RE = re.compile(r"\b(investigat\w*|deep dive|forensic|trace this|dig into)\b", re.IGNORECASE)


# Price/market queries get a market-data message.
_PRICE_INTENT_RE = re.compile(r"\b(price|cost|worth|value|market cap|mcap|how much|trading at)\b", re.IGNORECASE)

# News queries get a news-feed message.
_NEWS_INTENT_RE = re.compile(
    r"\b(news|latest|headline|happened|today|breaking|update|what'?s going on)\b", re.IGNORECASE
)

# Scam/security queries get a threat-intel message.
_SCAM_INTENT_RE = re.compile(r"\b(scam|rug|honeypot|exploit|hack|ponzi|fraud|safe|trust)\b", re.IGNORECASE)


def _thinking_template(text: str, has_address: bool) -> str:
    """Pick the contextual thinking placeholder for a question.

    Address questions get the scanner-data message (scan context is
    pre-fetched and prepended to the prompt), investigate-style intents get
    the research-collections message, everything else gets the general
    intelligence-database message.
    """
    if has_address:
        return _THINKING_ADDRESS
    if _INVESTIGATE_INTENT_RE.search(text):
        return _THINKING_INVESTIGATE
    if _SCAM_INTENT_RE.search(text):
        return _THINKING_SCAM
    if _NEWS_INTENT_RE.search(text):
        return _THINKING_NEWS
    if _PRICE_INTENT_RE.search(text):
        return _THINKING_PRICE
    return _THINKING_GENERAL


_STILL_DIGGING = (
    "\U0001f916 <b>Still digging...</b>\n\n\u23f3 Cross-referencing exploit databases + checking live news..."
)


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

    u = cast("dict[str, Any]", db.get_or_create_user(user.id, user.username, user.first_name))
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

    # Thumbs up + typing + preliminary message — instant feedback
    with contextlib.suppress(Exception):
        await message.chat.send_action("typing")
    with contextlib.suppress(Exception):
        from telegram import ReactionTypeEmoji

        await message.set_reaction([ReactionTypeEmoji(emoji="👍")])

    # Preliminary "thinking" message — keeps user engaged
    preliminary = await message.reply_text(
        "\U0001f916 <b>Thinking...</b>\n\n"
        f"\U0001f4ac <i>{escape(text[:150])}</i>\n\n"
        "\u23f1\ufe0f Working on a detailed answer...",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # CONCURRENT: fetch daily context + scan context + LLM call all at once
    addresses = _extract_addresses(text)
    history = cast("list[dict[str, str]]", db.get_chat_history(user.id))

    # Start all async work concurrently
    daily_ctx_task = asyncio.create_task(get_daily_context())
    scan_ctx_task = asyncio.create_task(_fetch_scan_context(addresses[0])) if addresses else None

    daily_context = await daily_ctx_task
    scan_summary = await scan_ctx_task if scan_ctx_task else None

    prompt = text
    if scan_summary:
        prompt = f"{scan_summary}\n\n{prompt}"
    if daily_context:
        prompt = f"{daily_context}\n\n{prompt}"

    # Call local LLM (Qwen — fast, no backend cost)
    result = await _call_local_llm(prompt, user.id, history)
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
    db.increment_usage(user.id, ai_msg=True)
    db.add_chat_message(user.id, "user", text)
    db.add_chat_message(user.id, "assistant", answer)
    with contextlib.suppress(Exception):
        await track_ai_query()
        await track_tokens(len(answer) // 4)

    # Replace thinking message with real answer
    try:
        await preliminary.edit_text(
            answer,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        await _send_answer(message, answer, result.get("rag_count", 0))
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

    u = cast("dict[str, Any]", db.get_or_create_user(user.id, user.username, user.first_name))
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


async def _send_answer(message: Message, answer: str, rag_count: int) -> None:
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
    ``X-RAG-Context``); counts above 100 are shown as "hundreds of sources".
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
                reply_markup=back_kb() if i == last else None,
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
    "SCAN_ENDPOINT",
    "SCAN_TIMEOUT",
    "ai_chat_handler",
    "chat_ai_response",
    "local_ai_response",
    "register_chat_ai",
]
