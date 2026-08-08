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
from app.domains.telegram.rugmunchbot.flood_control import check_flood, refund_flood
from app.domains.telegram.rugmunchbot.formatters import (
    TOKEN_RE,
    detect_chain,
    footer_links,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    short_addr,
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
    if chat is None or admin_kb_is_sent(chat.id):
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
    """POST /api/v1/chat — backend RAG-grounded AI answer.

    Returns a dict with ``message``, ``rag_count`` (from the X-RAG-Context
    header), ``tokens``, ``model``, ``provider`` and ``latency_ms``, or
    ``None`` on any failure/timeout.

    No ``context_override`` is sent so the backend runs its own RAG search
    (threat_intel, scam_intel, news_v2 + scanner_v2 when the message contains
    a token address) — that is what makes answers news-aware. The explicit
    per-user ``history`` overrides the shared backend bucket and the telegram
    user id is forwarded as X-Forwarded-For so the backend per-IP chat rate
    limiter keys per user (same pattern as the deprecated ai_chat.chat_synthesis).
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
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    body = cast("dict[str, Any]", raw)
    answer = (body.get("message") or "").strip()
    if not answer:
        return None

    rag_count = 0
    raw_rag = r.headers.get("X-RAG-Context", "0")
    try:
        rag_count = int(raw_rag) if raw_rag else 0
    except (TypeError, ValueError):
        rag_count = 0

    usage: Any = body.get("usage") or {}
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
            f"\U0001f517 Address: <code>{short_addr(address)}</code>\n\n"
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
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
            r"gm|gn|what'?s up|welcome)\b",
            re.IGNORECASE,
        ),
        (
            "\U0001f44b <b>Hey there!</b>\n\n"
            "I'm <b>RugMunch</b> \u2014 veteran crypto analyst. "
            "I scan tokens for scams, analyze wallets, track prices, and "
            "back every read with on-chain evidence across 77+ chains.\n\n"
            "\U0001f6e1\ufe0f <b>Try these:</b>\n"
            "\u2022 Paste a contract address \u2192 auto scan\n"
            "\u2022 <code>$TOKEN</code> \u2192 instant price\n"
            "\u2022 <code>/help</code> \u2192 all commands"
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
            r"\b(help|how|what|can you|capabilities|features|commands|what do|"
            r"what are|guide|tutorial|docs|documentation)\b",
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
            "\U0001f525 <code>/trending</code> \u2014 Hot tokens\n"
            "\U0001f4f0 <code>/news</code> \u2014 Crypto headlines\n"
            "\U0001f393 <code>/scamschool</code> \u2014 Learn scam patterns\n"
            "\U0001f451 <code>/pricing</code> \u2014 Upgrade your tier"
            f"{footer_links()}"
        ),
    ),
]


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
    "\U0001f525 <code>/trending</code> \u2014 hot tokens\n"
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

_THINKING_TEMPLATE = (
    "\U0001f916 <b>Thinking...</b>\n\n\U0001f4ac <i>{question}</i>\n\n\u23f3 Searching threat intel + news..."
)


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
    try:
        await message.chat.send_action("typing")
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

    # Gibberish gate — don't burn backend LLM tokens on noise. Falls through
    # to the offline tier-2 local intent / tier-3 redirect (ported from the
    # old message.py pipeline).
    if not _is_meaningful_question(text):
        if await _reply_local_intent(message, text, user):
            return True
        await _reply_redirect(message, user, flood_receipt)
        return True

    await message.chat.send_action("typing")

    placeholder = await message.reply_text(
        _THINKING_TEMPLATE.format(question=escape(text[:200])),
        parse_mode=ParseMode.HTML,
    )

    # Per-user session: telegram user id keys backend rate limiting
    # (X-Forwarded-For) AND the explicit conversation history (bot DB), so the
    # shared backend bucket never contaminates answers.
    history = cast("list[dict[str, str]]", db.get_chat_history(user.id))  # last 5, oldest-first

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
        # Backend down: try the offline tier-2 canned answers before falling
        # back to the honest unavailability message.
        if await _reply_local_intent(message, text, user):
            return True
        logger.info("chat_ai backend unavailable user=%s text=%.60s", user.id, text)
        try:
            await placeholder.edit_text(
                _FALLBACK_MESSAGE,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            logger.debug("chat_ai fallback send failed", exc_info=True)
        return True

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
