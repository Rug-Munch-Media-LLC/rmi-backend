#!/usr/bin/env python3
"""
RugMunchBot -- Backend RAG-wired AI chat handler (free text).

New v5+ chat surface: when a user sends free text (not a command, not a bare
address), this handler calls ``POST /api/v1/chat`` on the backend so answers
are grounded in the full RAG knowledge base — threat intel, scam patterns,
coding knowledge and, crucially, ``news_v2``/``news_articles`` — making
answers news-aware without any bot-side RAG plumbing.

Design notes:

* Backend auto-RAG — the handler deliberately does NOT send
  ``context_override``. When ``context_override.system_context`` is absent the
  backend runs its own ``_search_rag_context`` (threat_intel, scam_intel,
  news_v2 + scanner_v2 when the message contains a token address), so every
  answer is grounded. The ``X-RAG-Context`` response header reports how many
  hits grounded the answer (surfaced to the user as "Grounded in N sources").

* Per-user session — the backend ChatRequest has no ``session_id`` field; the
  Telegram user id is mapped two ways: ``X-Forwarded-For: <user_id>`` keys the
  backend per-IP chat rate limiter per user, and explicit ``history`` from the
  bot SQLite DB (last 5 messages) overrides the shared backend history bucket
  so conversations stay per-user (see ai_chat.chat_synthesis).

* Address enhancement — when free text contains an EVM ``0x...`` or Solana
  base58 address, a quick ``POST /api/v1/bot/scan`` read (Redis-cached, short
  timeout) is pulled and a compact risk summary is PREPENDED to the question,
  e.g. ``Context: token 0x1234...5678 scan shows risk_score=82,
  honeypot=True, ...``. Best-effort: any scan failure just skips the context.

* Output — LLM text is not trusted HTML; each chunk is HTML-escaped before
  sending (matches ask.py / message.py). Long answers are split on paragraph
  boundaries into ≤3800-char chunks (raw text, pre-escape, so escaped entities
  are never cut in half).

Registration (one line for bot.py):

    from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
    register_chat_ai(app)

WIRING NOTE: python-telegram-bot dispatches to the FIRST matching handler. If
bot.py keeps ``MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)``
this handler must be registered BEFORE it, or the free-text tail of
``handle_message`` must call :func:`ai_chat_handler` directly (it has the same
(update, ctx) signature as ``handlers.message.ai_chat_response``).
"""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.admin_state import track_ai_query, track_tokens
from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.flood_control import check_flood
from app.domains.telegram.rugmunchbot.formatters import detect_chain, paywall_text, short_addr
from app.domains.telegram.rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb
from app.domains.telegram.rugmunchbot.states import check_spam

if TYPE_CHECKING:
    from telegram import Message, Update
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS
# ══════════════════════════════════════════════
CHAT_ENDPOINT = f"{BACKEND_URL}/api/v1/chat"  # RAG-grounded AI chat (news-aware)
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"  # fast Redis-cached token scan
CHAT_TIMEOUT = 30.0  # backend AI synthesis budget (task requirement: >30s = fail)
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit
CHUNK_MAX_CHARS = 3800  # Telegram hard limit is 4096; leave headroom for escape expansion
MAX_ADDRESS_HINTS = 2  # cap address-triggered scans per message

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
    return f"Context: token {short_addr(address)} scan shows {', '.join(flags)}"


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
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    return _format_scan_summary(address, data)


# ══════════════════════════════════════════════
# BACKEND CHAT CALL
# ══════════════════════════════════════════════


async def _call_backend_chat(
    message: str,
    user_id: int,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """POST /api/v1/chat — backend RAG-grounded AI answer.

    Returns a dict with ``message``, ``rag_count`` (from the X-RAG-Context
    header), ``tokens``, ``model``, ``provider`` and ``latency_ms``, or
    ``None`` on any failure/timeout.

    No ``context_override`` is sent so the backend runs its own RAG search
    (threat_intel, scam_intel, news_v2 + scanner_v2 when the message contains
    a token address) — that is what makes answers news-aware. The explicit
    per-user ``history`` overrides the shared backend bucket and the telegram
    user id is forwarded as X-Forwarded-For so the backend per-IP chat rate
    limiter keys per user (same pattern as ai_chat.chat_synthesis).
    """
    payload: dict[str, Any] = {
        "message": message[:MAX_MESSAGE_CHARS],
        "history": list(history) if history else [],
        "tier": "T2",
        "temperature": 0.7,
        "max_tokens": 1024,
    }
    headers = backend_headers()
    headers["X-Forwarded-For"] = str(user_id)
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
            r = await client.post(CHAT_ENDPOINT, headers=headers, json=payload)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("backend chat unavailable (user=%s): %s", user_id, e)
        return None
    if r.status_code != 200:
        logger.debug("backend chat HTTP %d (user=%s)", r.status_code, user_id)
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    answer = (body.get("message") or "").strip()
    if not answer:
        return None

    rag_count = 0
    raw_rag = r.headers.get("X-RAG-Context", "0")
    try:
        rag_count = int(raw_rag) if raw_rag else 0
    except (TypeError, ValueError):
        rag_count = 0

    usage = body.get("usage") or {}
    tokens = 0
    try:
        tokens = int(usage.get("total_tokens") or usage.get("totalTokens") or 0)
    except (TypeError, ValueError):
        tokens = 0

    return {
        "message": answer,
        "rag_count": rag_count,
        "tokens": tokens,
        "model": body.get("model", ""),
        "provider": body.get("_provider", ""),
        "latency_ms": body.get("latency_ms", 0),
    }


# ══════════════════════════════════════════════
# OUTPUT CHUNKING (raw text first, then escape)
# ══════════════════════════════════════════════


def _split_oversized(paragraph: str, max_len: int) -> list[str]:
    """Hard-split a single oversized paragraph on the last fitting word."""
    if len(paragraph) <= max_len:
        return [paragraph]
    parts: list[str] = []
    remaining = paragraph
    while len(remaining) > max_len:
        cut = remaining.rfind(" ", 0, max_len)
        if cut < max_len // 2:
            cut = max_len  # no good word boundary — cut exactly
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _chunk_text(text: str, max_len: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized chunks on paragraph boundaries.

    Operates on the RAW (pre-escape) text: paragraphs are the safest split
    points and no chunk ever cuts through an HTML entity that escaping would
    later introduce (e.g. ``&lt;``).
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        for piece in _split_oversized(paragraph, max_len):
            if current and len(current) + len(piece) + 2 > max_len:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


# ══════════════════════════════════════════════
# GIBBERISH GATE (mirrors handlers/message.py tier-1b gate)
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
# HANDLER
# ══════════════════════════════════════════════

_INJECTION_WARNING = (
    "\U0001f6e1\ufe0f <b>RugMunch Anti-Abuse</b>\n\n"
    "I can't process that message. Ask me a normal crypto question instead."
)

_FALLBACK_MESSAGE = (
    "\U0001f9e0 <b>AI brain temporarily unavailable</b>\n\n"
    "I couldn't reach the intelligence engine right now. Use "
    "<code>/scan &lt;address&gt;</code> for direct analysis, or try again "
    "in a moment."
)

_SLOW_DOWN = "\U0001f422 <b>Slow down!</b>\n\nYou're sending messages too fast. Please wait {retry}s and try again."


async def ai_chat_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text → backend RAG chat (news-aware, knowledge-grounded answers).

    Same (update, ctx) signature as ``handlers.message.ai_chat_response`` so
    it can be wired either as a MessageHandler via :func:`register_chat_ai` or
    called directly from the free-text pipeline.
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

    # AI chat can be toggled independently of the rest of the bot (admin).
    if db.get_bot_state("chat_enabled", "on") != "on":
        await message.reply_text(
            "\U0001f4ac <b>AI Chat is temporarily disabled</b>\n\n"
            "The chat feature is under maintenance. Commands like /scan, /wallet "
            "and /price still work \u2014 just send a contract address!",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(user.id),
        )
        return

    # v6 anti-abuse: never forward prompt-injection attempts to the LLM.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked (chat_ai) user=%s category=%s", user.id, injection)
        try:
            await message.reply_text(_INJECTION_WARNING, parse_mode=ParseMode.HTML)
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            logger.debug("injection warning send failed (chat_ai)", exc_info=True)
        return

    # Weekly AI-message quota per tier (free 15 / scout 75 / hunter 250 / pro 500).
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        await message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    # Per-user flood limit (3/s, 30/min) — same surface as the rest of AI chat.
    flood_ok, retry_after, _receipt = await check_flood(user.id)
    if not flood_ok:
        await message.reply_text(
            _SLOW_DOWN.format(retry=retry_after),
            parse_mode=ParseMode.HTML,
        )
        return

    # Gibberish gate — don't burn LLM tokens on noise.
    if not _is_meaningful_question(text):
        logger.debug("chat_ai skipped gibberish user=%s text=%.40s", user.id, text)
        return

    await message.chat.send_action("typing")

    placeholder = await message.reply_text(
        "\U0001f916 <b>Thinking...</b>\n\n"
        f"\U0001f4ac <i>{escape(text[:200])}</i>\n\n"
        "\u23f3 Searching threat intel + news...",
        parse_mode=ParseMode.HTML,
    )

    # Per-user session: telegram user id keys backend rate limiting
    # (X-Forwarded-For) AND the explicit conversation history (bot DB), so the
    # shared backend bucket never contaminates answers.
    history = db.get_chat_history(user.id)  # last 5, oldest-first

    # Address enhancement: if the message contains a token address, ALSO pull a
    # quick scanner read and ground the question with a compact risk summary.
    prompt = text
    addresses = _extract_addresses(text)
    if addresses:
        summary = await _fetch_scan_context(addresses[0])
        if summary:
            prompt = f"{summary}\n\n{prompt}"

    result = await _call_backend_chat(prompt, user.id, history)
    if not result:
        logger.info("chat_ai backend unavailable user=%s text=%.60s", user.id, text)
        try:
            await placeholder.edit_text(
                _FALLBACK_MESSAGE,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            logger.debug("chat_ai fallback send failed", exc_info=True)
        return

    answer = result["message"]
    db.increment_usage(user.id, ai_msg=True)
    db.add_chat_message(user.id, "user", text)
    db.add_chat_message(user.id, "assistant", answer)
    try:
        await track_ai_query()
        await track_tokens(len(answer) // 4)
    except Exception:
        logger.debug("chat_ai analytics failed (user=%s)", user.id, exc_info=True)

    await _send_answer(message, answer, result.get("rag_count", 0))


async def _send_answer(message: Message, answer: str, rag_count: int) -> None:
    """Reply with the answer, chunked to Telegram's size limit.

    Chunking happens on the RAW text before HTML-escaping so we never split
    inside an escaped entity. LLM output is not trusted HTML: every chunk is
    escaped before sending (matches ask.py / message.py conventions). A small
    "Grounded in N sources" footer is appended to the last chunk when the
    backend reported RAG grounding.
    """
    chunks = _chunk_text(answer)
    if not chunks:
        return
    escaped = [escape(c) for c in chunks]
    if rag_count > 0:
        escaped[-1] = f"{escaped[-1]}\n\n<i>\u26a1 Grounded in {rag_count} intelligence source(s)</i>"

    last = len(escaped) - 1
    for i, chunk in enumerate(escaped):
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
    """Register the free-text backend-RAG chat handler on the bot Application.

    One-line wiring for bot.py:

        from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
        register_chat_ai(app)

    WIRING NOTE: python-telegram-bot dispatches to the FIRST matching handler,
    so if bot.py keeps the existing
    ``MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)`` this
    handler must be registered BEFORE it, or the free-text tail of
    ``handle_message`` must call :func:`ai_chat_handler` directly.
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
    "register_chat_ai",
]
