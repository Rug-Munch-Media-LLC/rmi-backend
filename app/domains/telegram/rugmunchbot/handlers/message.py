#!/usr/bin/env python3
"""
RugMunchBot -- Message handler + AI chat system (v5).

Auto-detect contract addresses, $TOKEN lookups, and tiered AI chat:

  Tier 1   RAG search -> LLM synthesis (grounded answer)
  Tier 1b  LLM general synthesis (server auto-grounds; full-range answers)
  Tier 2   Local intent matching (fast canned replies)
  Tier 3   Friendly redirect (catch-all help)

Free text that is not a command or an address becomes a chat message. The
same pipeline powers /chat <question> (handlers/chat.py) via ai_chat_response.
"""

from __future__ import annotations

import re
from html import escape
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.admin_state import (
    track_ai_query,
    track_message,
    track_tokens,
)
from app.domains.telegram.rugmunchbot.ai_chat import (
    chat_synthesis,
    format_rag_context,
    search_rag,
)
from app.domains.telegram.rugmunchbot.config import (
    DEXSCREENER,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.flood_control import check_flood, refund_flood
from app.domains.telegram.rugmunchbot.formatters import (
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    footer_links,
    is_evm,
    is_sol,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb
from app.domains.telegram.rugmunchbot.states import check_spam, should_skip_llm

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    if check_spam(user.id):
        return
    db.record_activity(msg=True)

    # Admin kill-switch: whole bot disabled (synced from the admin API).
    if db.get_bot_state("bot_enabled", "on") != "on":
        return

    # Analytics: count every inbound message handled (Redis, for the admin API).
    await track_message()

    # ══════════════════════════════════════════════
    # v6 anti-abuse: prompt injection + known scam addresses
    # ══════════════════════════════════════════════
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return

    # Block known scam addresses embedded in the message (never auto-scan them).
    evm_match = EVM_RE.search(text)
    sol_match = SOL_RE.search(text)
    for addr in (evm_match.group() if evm_match else None, sol_match.group() if sol_match else None):
        if addr and anti_abuse.is_known_scam_address(addr):
            logger.info("known-scam address blocked user=%s addr=%.16s", user.id, addr)
            await _scam_address_warning(update, addr)
            return

    # AI chat can be toggled independently of the rest of the bot (admin).
    if db.get_bot_state("chat_enabled", "on") != "on":
        await update.message.reply_text(
            "\U0001f4ac <b>AI Chat is temporarily disabled</b>\n\n"
            "The chat feature is under maintenance. Commands like /scan, /wallet "
            "and /price still work \u2014 just send a contract address!",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(user.id),
        )
        return

    # Auto-detect contract address (bare address)
    if is_evm(text) or is_sol(text):
        ctx.args = [text]
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return

    # Token symbol detection ($TOKEN)
    token_match = TOKEN_RE.search(text)
    if token_match:
        symbol = token_match.group(1).upper()
        await update.message.chat.send_action("typing")
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
                            from app.domains.telegram.rugmunchbot.formatters import web_scan_button

                            await update.message.reply_text(
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
                            return
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
            TelegramError,
        ) as e:
            logger.debug(f"handle_message $TOKEN lookup failed for {symbol}: {e}")

    # Natural language address extraction
    evm_match = EVM_RE.search(text)
    sol_match = SOL_RE.search(text)
    if evm_match:
        ctx.args = [evm_match.group()]
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return
    if sol_match:
        ctx.args = [sol_match.group()]
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return

    # Free text -> AI chat (tiered)
    await ai_chat_response(update, ctx, text, user)


async def ai_chat_response(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, user) -> bool:
    """Run the full AI chat tier pipeline. Returns True when a reply was sent.

    Shared by the free-text handler and /chat <question>. Enforces the
    weekly AI-message quota per tier and per-user flood limits.
    """
    if not text.strip():
        return False

    # v6 anti-abuse: never forward prompt-injection attempts to the LLM.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked (chat) user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return True

    # Tier gate: weekly AI message quota (free: 15, scout: 75, hunter: 250, pro: 500)
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        from app.domains.telegram.rugmunchbot.formatters import paywall_text

        await update.message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return True

    # Flood control: max 3 msgs/sec and 20 msgs/min per user on the AI chat
    # surface (see flood_control.py for the testable contract). /start and
    # /help are CommandHandler commands and never reach this handler; tier-3
    # redirects are refunded below so they are not counted against the limit.
    flood_ok, retry_after, flood_receipt = await check_flood(user.id)
    if not flood_ok:
        logger.info("AI chat flood-limited user=%s retry=%ss", user.id, retry_after)
        await update.message.reply_text(
            "\U0001f422 <b>Slow down!</b>\n\n"
            "You're sending messages too fast. "
            f"Please wait {retry_after}s and try again.",
            parse_mode=ParseMode.HTML,
        )
        return True

    await update.message.chat.send_action("typing")

    # Conversation context (last 5 messages) for grounded follow-up questions.
    history = db.get_chat_history(user.id, limit=db.CHAT_HISTORY_CONTEXT)

    # Tier 1: RMI RAG search -> LLM synthesis
    rag_hits = await search_rag(text, top_k=5)
    if rag_hits:
        rag_context = format_rag_context(rag_hits[:5])
        ai_answer = await chat_synthesis(text, rag_context, user_id=user.id, history=history)
        if ai_answer:
            logger.info("AI chat tier=1 (rag+llm) user=%s text=%.60s", user.id, text)
            db.increment_usage(user.id, ai_msg=True)
            db.add_chat_message(user.id, "user", text)
            db.add_chat_message(user.id, "assistant", ai_answer)
            await track_ai_query()
            await track_tokens(len(ai_answer) // 4)
            # LLM output is not trusted HTML — escape it so raw <...> in the
            # answer can't crash with Telegram "Can't parse entities".
            await update.message.reply_text(
                escape(ai_answer[:4000]),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
                disable_web_page_preview=True,
            )
            return True
        # LLM synthesis failed/timeout but RAG returned grounding — degrade
        # gracefully to the best raw hit so the user still gets an answer.
        best_hit = (rag_hits[0].get("content") or "").strip()
        if best_hit:
            logger.info("AI chat tier=1 (rag-raw fallback) user=%s text=%.60s", user.id, text)
            db.increment_usage(user.id, ai_msg=True)
            db.add_chat_message(user.id, "user", text)
            db.add_chat_message(user.id, "assistant", best_hit)
            await track_ai_query()
            await track_tokens(len(best_hit) // 4)
            await update.message.reply_text(escape(best_hit[:4000]), parse_mode=ParseMode.HTML, reply_markup=back_kb())
            return True

    # Tier 1b: LLM general synthesis (full-range assistant answers). Skipped
    # for gibberish/super-short text and rate-limited per user (~1/10s) so a
    # flood cannot hammer the LLM. The backend auto-grounds these calls with
    # its own RAG search when no bot-side context was found.
    if _looks_like_chat(text) and not should_skip_llm(user.id):
        ai_answer = await chat_synthesis(text, None, user_id=user.id, history=history)
        if ai_answer:
            logger.info("AI chat tier=1b (llm general) user=%s text=%.60s", user.id, text)
            db.increment_usage(user.id, ai_msg=True)
            db.add_chat_message(user.id, "user", text)
            db.add_chat_message(user.id, "assistant", ai_answer)
            await track_ai_query()
            await track_tokens(len(ai_answer) // 4)
            await update.message.reply_text(
                escape(ai_answer[:4000]),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
                disable_web_page_preview=True,
            )
            return True

    # Tier 2: Local intent matching
    local_response = local_ai_response(text, user)
    if local_response:
        logger.info("AI chat tier=2 (local intent) user=%s text=%.60s", user.id, text)
        db.increment_usage(user.id, ai_msg=True)
        await track_ai_query()
        await track_tokens(len(local_response) // 4)
        await update.message.reply_text(
            local_response,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
        return True

    # Tier 3: Friendly redirect (catch-all) — NOT counted against flood limits
    logger.info("AI chat tier=3 (redirect) user=%s text=%.60s", user.id, text)
    await refund_flood(user.id, flood_receipt)
    db.increment_usage(user.id, ai_msg=True)
    await track_ai_query()
    await update.message.reply_text(
        "\U0001f916 <b>RugMunch AI</b>\n\n"
        "I can help with token scans, wallet analysis, market data, DeFi, "
        "crypto news and even coding questions. Try:\n\n"
        "\U0001f4ac <code>/chat &lt;question&gt;</code> \u2014 talk to me about anything\n"
        "\U0001f50d <code>/scan &lt;address&gt;</code> \u2014 security audit\n"
        "\U0001f4cc <code>$TOKEN</code> \u2014 instant price lookup\n"
        "\U0001f9db <code>/wallet &lt;addr&gt;</code> \u2014 wallet analysis\n"
        "\U0001f525 <code>/trending</code> \u2014 hot tokens\n"
        "\U0001f4da <code>/scamschool</code> \u2014 learn scam patterns"
        f"{footer_links()}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(user.id),
        disable_web_page_preview=True,
    )
    return True


def _looks_like_chat(text: str) -> bool:
    """Heuristic gate for the LLM general-synthesis tier (tier 1b).

    Skips gibberish / one-liners that are not worth an LLM call, so a
    spammy flood cannot burn LLM tokens on noise.
    """
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    words = re.findall(r"[A-Za-z]{3,}", stripped)
    if not words:
        return False
    alpha = sum(1 for ch in stripped if ch.isalpha())
    if len(stripped) > 0 and alpha / len(stripped) < 0.35:
        return False
    # Repeated single word / character spam: "asdfasdfasdf", "lllll"
    return not (len(set(words)) == 1 and len(words) > 4)


# --- Local intent matching (Tier 2) ---

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
            "\U0001f4b0 I pull real-time data from DexScreener and CoinGecko."
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
            "I'm <b>RugMunch</b> \u2014 your crypto security sidekick. "
            "I scan tokens for scams, analyze wallets, track prices, and more across 77+ chains.\n\n"
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


def local_ai_response(text: str, user) -> str | None:
    """Tier 2: Match user intent against keyword patterns."""
    text_lower = text.lower().strip()
    for pattern, response in _INTENT_RULES:
        if pattern.search(text_lower):
            if len(text_lower) < 2:
                return None
            return response
    return None


# ── v6 anti-abuse reply helpers ──

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
    from app.domains.telegram.rugmunchbot.formatters import short_addr

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
