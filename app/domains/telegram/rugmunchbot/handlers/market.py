#!/usr/bin/env python3
"""
RugMunchBot -- Market data command handlers.

Commands: trending, news + helpers: _time_ago, _fetch_cg_trending_news
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    TELEGRAM_CHANNEL,
    WEBSITE_URL,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot")


def _time_ago(dt_iso: str | None) -> str:
    """Convert ISO timestamp to human readable relative time."""
    if not dt_iso:
        return ""
    try:
        dt_iso_clean = dt_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_iso_clean)
        diff = datetime.now(UTC) - dt
        total_secs = diff.total_seconds()
        if total_secs < 0:
            return "just now"
        if total_secs < 60:
            return f"{int(total_secs)}s ago"
        if total_secs < 3600:
            return f"{int(total_secs / 60)}m ago"
        if total_secs < 86400:
            return f"{int(total_secs / 3600)}h ago"
        return f"{diff.days}d ago"
    except (ConnectionError, OSError, TimeoutError):
        return ""


async def _fetch_cg_trending_news() -> list[dict] | None:
    """Fallback: fetch CoinGecko trending coins as pseudo-news items."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if r.status_code != 200:
                return None
            coins = r.json().get("coins", [])
            if not coins:
                return None
            items = []
            for c in coins[:5]:
                item = c.get("item", {})
                items.append(
                    {
                        "title": f"{item.get('name', 'Unknown')} ({item.get('symbol', '?')}) trending now",
                        "source": "Trending",
                        "published_at": None,
                        "url": f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
                        "score": item.get("score", 0) or 0,
                    }
                )
            return items
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("CoinGecko trending fallback failed: %s", exc)
        return None


async def cmd_rugcheck(update, ctx) -> None:
    """Quick rug pull checklist - educational."""
    from telegram.constants import ParseMode

    from app.domains.telegram.rugmunchbot.formatters import footer_links, sep

    text = (
        f"\U0001f6a9 <b>Rug Pull Checklist</b>\n{sep()}\n\n"
        f"Before buying ANY token, check these:\n\n"
        f"<b>\U0001f534 RED FLAGS (Run!)</b>\n{thin_sep()}\n"
        f"  \u274c Honeypot detected (can't sell)\n"
        f"  \u274c Unrenounced ownership\n"
        f"  \u274c Mintable supply (infinite tokens)\n"
        f"  \u274c Blacklist function exists\n"
        f"  \u274c LP not locked (< 50%)\n"
        f"  \u274c Buy/sell tax > 10%\n\n"
        f"<b>\U0001f7e1 YELLOW FLAGS (Caution)</b>\n{thin_sep()}\n"
        f"  \u26a0\ufe0f Bundle activity detected\n"
        f"  \u26a0\ufe0f >50% fresh wallets (<7 days)\n"
        f"  \u26a0\ufe0f Dev wallet holds >20%\n"
        f"  \u26a0\ufe0f Suspicious volume/mcap ratio\n"
        f"  \u26a0\ufe0f Very low liquidity vs mcap\n\n"
        f"<b>\U0001f7e2 GREEN FLAGS (Safer)</b>\n{thin_sep()}\n"
        f"  \u2705 Contract verified\n"
        f"  \u2705 Ownership renounced\n"
        f"  \u2705 LP locked > 90%\n"
        f"  \u2705 No blacklist/mint functions\n"
        f"  \u2705 Diverse holder distribution\n"
        f"  \u2705 Organic volume patterns\n\n"
        f"<b>\U0001f6e1\ufe0f Always run /scan before buying!</b>"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


# Chain aliases accepted by /trending (backend RugCharts chain filter)
RUGCHARTS_CHAIN_ALIASES = {
    "sol": "solana",
    "eth": "ethereum",
    "bnb": "bsc",
    "arb": "arbitrum",
    "tron": "tron",
}
TRENDING_CHAINS = "solana · ethereum · base · bsc · arbitrum · tron"


def _resolve_chain(arg: str | None) -> str:
    """Normalize a /trending chain argument (aliases → canonical names)."""
    if not arg:
        return "solana"
    chain = arg.lower().strip()
    return RUGCHARTS_CHAIN_ALIASES.get(chain, chain)


async def _fetch_rugcharts_trending(chain: str = "solana", limit: int = 8) -> list[dict] | None:
    """Fetch trending tokens from the RugCharts backend (fused multi-source data).

    Returns the token list (sorted by volume, pre-enriched with price/24h
    change/liquidity) or None on any failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/rugcharts/trending",
                params={"chain": chain, "limit": limit},
                headers=backend_headers(),
            )
            if r.status_code != 200:
                logger.debug("rugcharts trending status %s", r.status_code)
                return None
            body = r.json()
            data = body.get("data") or body
            tokens = data.get("tokens") or []
            return tokens if tokens else None
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("rugcharts trending fetch failed: %s", exc)
        return None


def _trending_display(token: dict) -> tuple[str, str]:
    """Return a display-safe (name, symbol), trimming long/garbage values."""
    raw_name = (token.get("name") or "").strip()
    symbol = (token.get("symbol") or "").strip()
    if not symbol:
        symbol = raw_name.split()[0][:12] if raw_name else "???"
    name = raw_name[:40] or "Unknown"
    return name, symbol


async def _build_trending_text(chain: str = "solana", limit: int = 8) -> str | None:
    """Shared trending renderer for /trending + menu/refresh callbacks.

    Returns the HTML message text, or None when no data is available.
    """
    tokens = await _fetch_rugcharts_trending(chain, limit)
    if not tokens:
        return None
    lines = [f"\U0001f525 <b>Trending \u2014 {chain.capitalize()}</b>", sep()]
    for i, t in enumerate(tokens[:limit], 1):
        addr = t.get("address", "")
        name, symbol = _trending_display(t)
        price = float(t.get("price_usd") or 0)
        h24 = float(t.get("change_24h") or 0)
        vol = float(t.get("volume_24h") or 0)
        emoji = "\U0001f7e2" if h24 >= 0 else "\U0001f534"
        lines.append(
            f"{i}. {emoji} <b>{name}</b> ({symbol})\n"
            f"   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}\n"
            f"   <code>{short_addr(addr)}</code>"
        )
    lines.append(f"\n<i>Chains: {TRENDING_CHAINS}</i>")
    lines.append(footer_links())
    return "\n".join(lines)


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trending [chain] — hot tokens from the RugCharts engine."""
    chain = _resolve_chain(ctx.args[0]) if ctx.args else "solana"
    msg = await update.message.reply_text("\U0001f525 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML)
    text = await _build_trending_text(chain, limit=8)
    if text is None:
        await msg.edit_text(
            "\u274c <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )
        return
    trending_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f504 Refresh", callback_data=f"trending_refresh_{chain}")],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    await msg.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=trending_kb,
        disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════
# MARKET INTELLIGENCE (/market /movers /launches /whales)
# Backed by the backend market-intel router (app/routers/market_intel).
# The backend caches every endpoint (CoinGecko/DexScreener TTL) — the bot
# does NOT add its own cache layer.
# ══════════════════════════════════════════════

MARKET_UNAVAILABLE = (
    "\u26a0\ufe0f <b>Market Data Unavailable</b>\n\n"
    "Market data is temporarily unavailable.\nPlease try again in a moment."
)


def _fmt_usd(n: float | None) -> str:
    """Compact USD with trillions support (backend returns raw USD floats).

    Handles 3.2e12 → "$3.20T", 67_234 → "$67,234.10", 0.00001234 → "$0.00001234".
    Falls back to fmt_number conventions for everything else.
    """
    if n is None:
        return "N/A"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n)
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.4f}"
    if v >= 0.01:
        return f"${v:.6f}"
    return f"${v:.10f}"


async def _fetch_market_intel(
    path: str, params: dict[str, Any] | None = None, timeout_secs: float = 10
) -> dict[str, Any] | None:
    """GET a backend market-intel endpoint with the shared-secret header.

    Returns the parsed JSON body or None on any failure (network, non-200,
    malformed body). Callers render a graceful unavailable state on None.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            r = await client.get(
                f"{BACKEND_URL}{path}",
                params=params,
                headers=backend_headers(),
            )
            if r.status_code != 200:
                logger.debug("market intel %s status %s", path, r.status_code)
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("market intel %s fetch failed: %s", path, exc)
        return None


def _menu_kb() -> InlineKeyboardMarkup:
    """Shared bottom bar: back to the main menu."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]])


def _change_emoji(change: float) -> str:
    return "\U0001f7e2" if change >= 0 else "\U0001f534"


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /market — global market overview (mcap, volume, dominance, fear/greed, BTC/ETH)."""
    msg = await update.message.reply_text("\U0001f4ca <b>Fetching market overview...</b>", parse_mode=ParseMode.HTML)
    overview, macro = await asyncio.gather(
        _fetch_market_intel("/api/v1/market/overview"),
        _fetch_market_intel("/api/v1/market/macro"),
    )
    if not overview:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(f"\U0001f310 Total Market Cap: <b>{_fmt_usd(overview.get('total_market_cap'))}</b>")
    if overview.get("total_volume_24h") is not None:
        lines.append(f"\U0001f4c8 24h Volume: <b>{_fmt_usd(overview.get('total_volume_24h'))}</b>")
    if overview.get("btc_dominance") is not None:
        lines.append(f"\u20bf BTC Dominance: <b>{float(overview['btc_dominance']):.1f}%</b>")
    if overview.get("eth_dominance") is not None:
        lines.append(f"\u27e0 ETH Dominance: <b>{float(overview['eth_dominance']):.1f}%</b>")
    active = overview.get("active_cryptocurrencies")
    if active:
        with contextlib.suppress(TypeError, ValueError):
            lines.append(f"\U0001fa99 Active Coins: <b>{int(active):,}</b>")

    fg = overview.get("fear_greed") or {}
    if fg.get("value") is not None:
        fg_value = int(fg.get("value", 0))
        fg_class = fg.get("classification") or "Neutral"
        fg_emoji = (
            "\U0001f525"
            if fg_value >= 75
            else "\U0001f60d"
            if fg_value >= 60
            else "\U0001f610"
            if fg_value >= 40
            else "\U0001f631"
        )
        lines.append("")
        lines.append(f"{fg_emoji} Fear & Greed: <b>{fg_value}</b> ({fg_class})")

    if macro and macro.get("items"):
        lines.append("")
        for it in macro["items"][:2]:
            if it.get("current_price") is None:
                continue
            asset = it.get("asset", "")
            change = it.get("change_24h")
            change_txt = f" ({fmt_pct(change)})" if change is not None else ""
            lines.append(f"\U0001f4b0 <b>{asset}:</b> {_fmt_usd(it.get('current_price'))}{change_txt}")

    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


async def cmd_movers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /movers — top 24h gainers & losers from the backend market router."""
    msg = await update.message.reply_text("\U0001f680 <b>Fetching market movers...</b>", parse_mode=ParseMode.HTML)
    data = await _fetch_market_intel("/api/v1/market/movers", params={"limit": 20})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    # Backend orders desc by 24h change; split into gainers/losers from that window.
    gainers = [i for i in items if (i.get("change_24h") or 0) >= 0][:6]
    losers = [i for i in items if (i.get("change_24h") or 0) < 0][:6]

    lines = ["\U0001f680 <b>24h Market Movers</b>", sep()]
    lines.append("\U0001f7e2 <b>TOP GAINERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(gainers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = item.get("change_24h") or 0
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {_fmt_usd(item.get('price'))} | Vol: {_fmt_usd(item.get('volume_24h'))}"
        )
    if not gainers:
        lines.append("<i>No gainers in the current window.</i>")

    lines.append("")
    lines.append("\U0001f534 <b>TOP LOSERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(losers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = item.get("change_24h") or 0
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {_fmt_usd(item.get('price'))} | Vol: {_fmt_usd(item.get('volume_24h'))}"
        )
    if not losers:
        lines.append("<i>No losers in the top movers \u2014 market is green \U0001f7e2</i>")

    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


async def cmd_launches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /launches — new token launches from the backend market router."""
    msg = await update.message.reply_text("\U0001f195 <b>Fetching new launches...</b>", parse_mode=ParseMode.HTML)
    data = await _fetch_market_intel("/api/v1/market/launches", params={"limit": 8})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f195 <b>New Token Launches</b>", sep()]
    for i, item in enumerate(items[:8], 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:28]
        change = item.get("change_24h") or 0
        chain_txt = fmt_chain(item.get("chain") or "solana")
        bundler = "\U0001f6a8 bundler" if item.get("bundler_detected") else ""
        addr = item.get("address") or ""
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol}) {chain_txt}\n"
            f"   {_fmt_usd(item.get('price'))} | <b>{fmt_pct(change)}</b> | Vol: {_fmt_usd(item.get('volume_24h'))}"
            f"{f' | {bundler}' if bundler else ''}"
        )
        if addr:
            lines.append(f"   <code>{short_addr(addr)}</code>")
    lines.append("\n<i>Fresh launches \u2014 always /scan before buying.</i>")
    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /whales — recent whale moves from the backend market router."""
    msg = await update.message.reply_text("\U0001f40b <b>Fetching whale activity...</b>", parse_mode=ParseMode.HTML)
    data = await _fetch_market_intel("/api/v1/market/whales", params={"limit": 5})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f40b <b>Whale Activity</b>", sep()]
    for i, item in enumerate(items[:5], 1):
        symbol = (item.get("token_symbol") or "???").upper()[:12]
        action = (item.get("type") or "buy").lower()
        action_txt = "BUY" if action == "buy" else "SELL"
        action_emoji = "\U0001f7e2" if action == "buy" else "\U0001f534"
        smart = " \U0001f4a1 smart money" if item.get("is_smart_money") else ""
        chain_raw = item.get("chain") or ""
        chain_txt = f" {fmt_chain(chain_raw)}" if chain_raw else ""
        addr = item.get("token_address") or ""
        lines.append(
            f"{i}. {action_emoji} <b>{action_txt} {symbol}</b>{chain_txt}{smart}\n"
            f"   {_fmt_usd(item.get('value_usd'))}"
            f"{f' | <code>{short_addr(addr)}</code>' if addr else ''}"
        )
    lines.append("\n<i>Large moves detected across Solana/Ethereum/Bitcoin pairs.</i>")
    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


def register_market(app: Application) -> None:
    """Register market-awareness commands on the bot application.

    Wires /market, /trending, /movers, /launches and /whales in one call.
    The Lead wires this from bot.py; replace the old per-command registrations
    (e.g. ``_reg(app, "trending", cmd_trending)``) with this call so commands
    are not double-registered.
    """
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("movers", cmd_movers))
    app.add_handler(CommandHandler("launches", cmd_launches))
    app.add_handler(CommandHandler("whales", cmd_whales))


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)
    articles = []
    source_label = "RMI News Network"

    # Try the internal news-feed endpoint first (has bias/sentiment/factual ratings)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/internal/news-feed",
                params={"channel": "news", "limit": 5, "min_score": 0},
                headers=backend_headers(),
            )
            if r.status_code == 200:
                body = r.json()
                articles = body.get("items", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("cmd_news internal feed failed: %s", e)

    # Fallback to backend trending news
    if not articles:
        source_label = "Trending"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
                    headers=backend_headers(),
                )
                if r.status_code == 200:
                    body = r.json()
                    articles = body.get("items", [])
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("cmd_news backend fetch failed: %s", e)

    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "Trending"

    news_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
            [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )

    if articles:
        bias_emoji = {
            "left": "\U0001f535",
            "center-left": "\U0001f537",
            "center": "\u26aa",
            "center-right": "\U0001f536",
            "right": "\U0001f534",
        }
        lines = ["\U0001f4f0 <b>Rated Crypto News</b>", f"<i>via {source_label}</i>", sep()]
        for a in articles[:5]:
            title = (a.get("title") or "Untitled")[:100]
            source = a.get("source", "News")
            published = _time_ago(a.get("published_at") or a.get("publishedAt"))
            pub_str = f" \u00b7 {published}" if published else ""
            url = a.get("url") or a.get("link") or ""
            title_linked = f'<a href="{url}">{title}</a>' if url else title

            # Bias/sentiment/factual ratings
            bias = a.get("bias", "center")
            sentiment = a.get("sentiment_score", 0)
            factual = a.get("factual_rating", "N/A")
            source_tier = a.get("source_tier", 5)
            sent_emoji = "\U0001f525" if sentiment > 30 else "\U0001f9ca" if sentiment < -30 else "\u2795"
            bias_e = bias_emoji.get(bias, "\u26aa")
            tier_str = "\u2605" * max(1, 6 - int(source_tier)) if isinstance(source_tier, (int, float)) else ""

            lines.append(
                f"{sent_emoji} {bias_e} [{tier_str}] {title_linked}\n"
                f"  <i>{source}</i>{pub_str} \u00b7 Factuality: {factual}"
            )
        lines.append(footer_links())
        text = "\n".join(lines)
    else:
        text = (
            f"\U0001f4f0 <b>Crypto News</b>\n\n"
            f"<i>News feed temporarily unavailable.</i>\n\n"
            f'\U0001f4e2 Follow <a href="{TELEGRAM_CHANNEL}">@RugMunchAlerts</a> for real-time alerts!\n'
            f'\U0001f310 <a href="{WEBSITE_URL}">rugmunch.io</a>'
        )

    with contextlib.suppress(Exception):
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=news_kb,
            disable_web_page_preview=True,
        )
