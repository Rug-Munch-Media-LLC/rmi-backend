#!/usr/bin/env python3
"""
RugMunchBot -- Market data command handlers.

Commands: trending, news + helpers: _time_ago, _fetch_cg_trending_news
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    TELEGRAM_CHANNEL,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    fmt_number,
    fmt_pct,
    footer_links,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

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
                items.append({
                    "title": f"{item.get('name', 'Unknown')} ({item.get('symbol', '?')}) trending on CoinGecko",
                    "source": "CoinGecko",
                    "published_at": None,
                    "url": f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
                    "score": item.get("score", 0) or 0,
                })
            return items
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("CoinGecko trending fallback failed: %s", exc)
        return None

async def cmd_rugcheck(update, ctx) -> None:
    """Quick rug pull checklist - educational."""
    from telegram.constants import ParseMode

    from app.domains.telegram.rugmunchbot.formatters import footer_links, sep, thin_sep
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

async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text(
        "\U0001f525 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            boosts_r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
            if boosts_r.status_code != 200:
                await msg.edit_text("\u274c Could not fetch trending data.", parse_mode=ParseMode.HTML)
                return
            boost_list = boosts_r.json()
            if not isinstance(boost_list, list) or not boost_list:
                await msg.edit_text("\u274c No trending data available.", parse_mode=ParseMode.HTML)
                return
            boost_list = boost_list[:8]

            enriched = {}
            addresses = {b.get("tokenAddress", "") for b in boost_list if b.get("tokenAddress")}
            for addr in addresses:
                try:
                    tr = await client.get(f"{DEXSCREENER}/tokens/{addr}", timeout=5)
                    if tr.status_code == 200:
                        pairs = tr.json().get("pairs", [])
                        if pairs:
                            enriched[addr] = pairs[0]
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.debug(f"Trending enrichment failed for {addr}: {e}")

            lines = ["\U0001f525 <b>Trending Tokens</b>", sep(), thin_sep()]
            for i, b in enumerate(boost_list, 1):
                addr = b.get("tokenAddress", "")
                name = b.get("name") or b.get("description", "???")[:15]
                symbol = b.get("symbol", "???")
                chain = b.get("chainId", "?")
                boost_count = b.get("boostCount", 0)
                boost_str = f" \U0001f525{boost_count}" if boost_count else ""
                pair = enriched.get(addr)
                if pair:
                    price = float(pair.get("priceUsd", 0))
                    h24 = pair.get("priceChange", {}).get("h24") or 0
                    vol = float(pair.get("volume", {}).get("h24") or 0)
                    emoji = "\U0001f7e2" if h24 >= 0 else "\U0001f534"
                    lines.append(
                        f"{i}. {emoji} <b>{name}</b> ({symbol}) {chain}\n"
                        f"   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}{boost_str}\n"
                        f"   <code>{short_addr(addr)}</code>"
                    )
                else:
                    lines.append(
                        f"{i}. \U0001f525 <b>{name}</b> ({symbol}) {chain}{boost_str}\n"
                        f"   <code>{short_addr(addr)}</code>"
                    )
            lines.append(footer_links())
            trending_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="trending_refresh")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ])
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=trending_kb,
                disable_web_page_preview=True,
            )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"cmd_trending failed: {e}")
        await msg.edit_text(
            "\u274c <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)
    articles = []
    source_label = "Backend"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5")
            if r.status_code == 200:
                body = r.json()
                articles = body.get("items", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("cmd_news backend fetch failed: %s", e)

    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "CoinGecko"

    news_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
        [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
    ])

    if articles:
        lines = ["\U0001f4f0 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
        for a in articles[:5]:
            title = (a.get("title") or "Untitled")[:100]
            source = a.get("source", "News")
            published = _time_ago(a.get("published_at") or a.get("publishedAt"))
            pub_str = f" \u00b7 {published}" if published else ""
            url = a.get("url") or a.get("link") or ""
            title_linked = f'<a href="{url}">{title}</a>' if url else title
            lines.append(f"\U0001f4f0 {title_linked}\n  <i>{source}</i>{pub_str}")
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
