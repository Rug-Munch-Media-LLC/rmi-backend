#!/usr/bin/env python3
"""
RugMunchBot -- Message handler + AI chat system.

Auto-detect contract addresses, $TOKEN lookups, and tiered AI chat
(RAG backend -> local intent matching -> friendly redirect).
"""

import re

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    EVM_RE,
    SOL_RE,
    TOKEN_RE,
    footer_links,
    is_evm,
    is_sol,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb
from app.domains.telegram.rugmunchbot.states import check_spam

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
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}")],
                                    [web_scan_button(addr, chain)],
                                ]),
                                disable_web_page_preview=True,
                            )
                            return
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, telegram.error.TelegramError) as e:
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

    # AI Chat -- Tiered fallback (RAG -> local intent -> friendly redirect)
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        from app.domains.telegram.rugmunchbot.formatters import paywall_text
        await update.message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    await update.message.chat.send_action("typing")

    # Tier 1: RMI RAG backend
    rag_response = await _try_rag_backend(text)
    if rag_response:
        logger.info("AI chat tier=1 (rag) user=%s text=%.60s", user.id, text)
        db.increment_usage(user.id, ai_msg=True)
        await update.message.reply_text(
            rag_response[:4000], parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return

    # Tier 2: Local intent matching
    local_response = local_ai_response(text, user)
    if local_response:
        logger.info("AI chat tier=2 (local intent) user=%s text=%.60s", user.id, text)
        db.increment_usage(user.id, ai_msg=True)
        await update.message.reply_text(
            local_response,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
        return

    # Tier 3: Friendly redirect (catch-all)
    logger.info("AI chat tier=3 (redirect) user=%s text=%.60s", user.id, text)
    db.increment_usage(user.id, ai_msg=True)
    await update.message.reply_text(
        "\U0001f916 <b>RugMunch AI</b>\n\n"
        "I can help with token scans, wallet analysis, and market data. "
        "Use <code>/scan &lt;address&gt;</code> or just paste a token address!\n\n"
        "\U0001f4a1 <b>Quick tips:</b>\n"
        "\u2022 <code>$TOKEN</code> \u2192 instant price lookup\n"
        "\u2022 <code>/wallet &lt;addr&gt;</code> \u2192 wallet analysis\n"
        "\u2022 <code>/trending</code> \u2192 hot tokens\n"
        "\u2022 <code>/scamschool</code> \u2192 learn scam patterns"
        f"{footer_links()}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )


async def _try_rag_backend(query: str) -> str | None:
    """Tier 1: Query RMI RAG backend. Returns response text or None."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BACKEND_URL}/api/v1/rag/v2/search",
                json={
                    "query": query,
                    "collection": "scam_intel",
                    "top_k": 5,
                    "min_similarity": 0.0,
                    "filters": {},
                },
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", [])
                if hits:
                    answer = hits[0].get("content", "")
                    if answer:
                        return answer
    except httpx.ConnectError:
        logger.debug("RAG backend unreachable (connection refused)")
    except httpx.TimeoutException:
        logger.debug("RAG backend timeout")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("RAG backend error: %s", e)
    return None


# --- Local intent matching (Tier 2) ---

_INTENT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(scan|check|audit|rug|scam|honeypot|safu|drain|phish|malicious|"
        r"dangerous|secure|risky|safe(ty)?|is this safe|is it safe|trustworthy)\b",
        re.IGNORECASE,
    ), (
        "\U0001f50d <b>Token Security Scan</b>\n\n"
        "Use <code>/scan &lt;address&gt;</code> to run a full security audit. "
        "I check for:\n"
        "\u2022 \U0001f36f Honeypot detection\n"
        "\u2022 \U0001f3c3 Rug pull risks\n"
        "\u2022 \U0001f4e6 Bundle / wash trading\n"
        "\u2022 \U0001f9db Dev wallet history\n"
        "\u2022 \U0001f4ca Liquidity & holder analysis\n\n"
        "Just paste any contract address and I'll auto-detect it!"
    )),
    (re.compile(
        r"\b(price|chart|worth|value|how much|cost|mc[aap]+|market cap|volume|"
        r"liquidity|pump|dump|buy|sell|token price|usd)\b",
        re.IGNORECASE,
    ), (
        "\U0001f4ca <b>Price & Market Data</b>\n\n"
        "Use <code>/quick &lt;symbol&gt;</code> or <code>$TOKEN</code> for instant prices.\n"
        "Use <code>/chart &lt;address&gt;</code> for technical analysis charts.\n"
        "Use <code>/scan &lt;address&gt;</code> for full market data.\n\n"
        "\U0001f4b0 I pull real-time data from DexScreener and CoinGecko."
    )),
    (re.compile(
        r"\b(wallet|portfolio|balance|holdings|my token|my coin|bag|"
        r"hodl|fund|money)\b",
        re.IGNORECASE,
    ), (
        "\U0001f9db <b>Wallet Analysis</b>\n\n"
        "Use <code>/wallet &lt;address&gt;</code> to analyze any wallet.\n"
        "Use <code>/portfolio</code> to view your tracked wallets.\n\n"
        "I show:\n"
        "\u2022 \U0001f4b0 Token balances & values\n"
        "\u2022 \U0001f504 Recent transaction history\n"
        "\u2022 \u26a0\ufe0f Risk metrics & scam checks\n"
        "\u2022 \U0001f517 Counterparty connections"
    )),
    (re.compile(
        r"\b(compare|vs |versus|better than|which (is|one)|difference)\b", re.IGNORECASE
    ), (
        "\u2696\ufe0f <b>Token Comparison</b>\n\n"
        "Use <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code> to pit two tokens "
        "side-by-side.\n\n"
        "I'll show you both risk scores, prices, liquidity, and holder metrics "
        "in one view!"
    )),
    (re.compile(
        r"\b(trending|hot|popular|what to buy|best coin|moon|gem|"
        r"next 100x|top token|gainers|movers)\b",
        re.IGNORECASE,
    ), (
        "\U0001f525 <b>Trending Tokens</b>\n\n"
        "Check <code>/trending</code> for the hottest tokens right now.\n"
        "I show real-time data on:\n"
        "\u2022 \U0001f4c8 Top gainers\n"
        "\u2022 \U0001f4a7 Highest volume\n"
        "\u2022 \U0001f195 New pairs\n"
        "\u2022 \U0001f3e0 Most holders"
    )),
    (re.compile(
        r"\b(alert|notify|watch|track|monitor|notification|remind|follow)\b",
        re.IGNORECASE,
    ), (
        "\U0001f514 <b>Price Alerts</b>\n\n"
        "Use <code>/watch &lt;address&gt;</code> to add tokens to your watchlist.\n"
        "Use <code>/alerts</code> to manage your price alerts.\n\n"
        "\U0001f4cc <b>Scout+</b> tier gets real-time push alerts for price moves, "
        "large transactions, and security events."
    )),
    (re.compile(
        r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
        r"gm|gn|what'?s up|welcome)\b",
        re.IGNORECASE,
    ), (
        "\U0001f44b <b>Hey there!</b>\n\n"
        "I'm <b>RugMunch</b> \u2014 your crypto security sidekick. "
        "I scan tokens for scams, analyze wallets, track prices, and more across 77+ chains.\n\n"
        "\U0001f6e1\ufe0f <b>Try these:</b>\n"
        "\u2022 Paste a contract address \u2192 auto scan\n"
        "\u2022 <code>$TOKEN</code> \u2192 instant price\n"
        "\u2022 <code>/help</code> \u2192 all commands"
        f"{footer_links()}"
    )),
    (re.compile(r"\b(thank|thanks|ty|thx|appreciate|good bot|nice|great)\b", re.IGNORECASE), (
        "\U0001f64c You're welcome! Happy to help keep the crypto space safe.\n\n"
        "If you find this useful, tell your friends! \n"
        "Use <code>/refer</code> to get your referral link."
    )),
    (re.compile(
        r"\b(help|how|what|can you|capabilities|features|commands|what do|"
        r"what are|guide|tutorial|docs|documentation)\b",
        re.IGNORECASE,
    ), (
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
    )),
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
