#!/usr/bin/env python3
"""
RugMunchBot -- /sentiment command: Market sentiment from own data + external.

Shows market sentiment trend, fear/greed index, social volume,
and crypto market health indicators aggregated from our data pipeline.
Scout+ tier feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    COINGECKO,
    WEBSITE_URL,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import paywall_kb

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.sentiment")


async def _fetch_market_overview() -> dict:
    """Fetch market overview from CoinGecko + own data."""
    result = {
        "btc_dominance": None,
        "total_mcap": None,
        "total_volume_24h": None,
        "fear_greed": None,
        "sentiment": "neutral",
        "trending_coins": [],
        "market_health": "unknown",
        "eth_gas": None,
        "btc_dominance_change": None,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # CoinGecko global data
            r = await client.get(f"{COINGECKO}/global")
            if r.status_code == 200:
                data = r.json().get("data", {})
                result["btc_dominance"] = data.get("market_cap_percentage", {}).get("btc")
                result["total_mcap"] = data.get("total_market_cap", {}).get("usd")
                result["total_volume_24h"] = data.get("total_volume", {}).get("usd")

            # Fear & Greed Index
            try:
                fg = await client.get("https://api.alternative.me/fng/?limit=1")
                if fg.status_code == 200:
                    fg_data = fg.json().get("data", [{}])[0]
                    result["fear_greed"] = {
                        "value": int(fg_data.get("value", 50)),
                        "classification": fg_data.get("value_classification", "Neutral"),
                    }
            except Exception:
                pass

            # ETH Gas (proxy for network activity)
            try:
                gas = await client.get("https://api.etherscan.io/api?module=gastracker&action=gasoracle")
                if gas.status_code == 200:
                    gas_data = gas.json().get("result", {})
                    result["eth_gas"] = gas_data.get("ProposeGasPrice")
            except Exception:
                pass

            # Try own backend sentiment data
            try:
                own = await client.get(f"{BACKEND_URL}/api/v1/market/sentiment", timeout=5)
                if own.status_code == 200:
                    own_data = own.json()
                    if isinstance(own_data, dict):
                        result["sentiment"] = own_data.get("sentiment", result["sentiment"])
                        result["btc_dominance_change"] = own_data.get("btc_dominance_change")
            except Exception:
                pass

    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("Market overview fetch failed: %s", e)

    # Derive market health
    fg_val = result.get("fear_greed", {}).get("value", 50) if result.get("fear_greed") else 50
    if fg_val >= 75:
        result["market_health"] = "extreme_greed"
    elif fg_val >= 60:
        result["market_health"] = "greed"
    elif fg_val >= 40:
        result["market_health"] = "neutral"
    elif fg_val >= 25:
        result["market_health"] = "fear"
    else:
        result["market_health"] = "extreme_fear"

    return result


def _health_emoji(health: str) -> str:
    emojis = {
        "extreme_greed": "\U0001f525",
        "greed": "\U0001f4c8",
        "neutral": "\u26aa",
        "fear": "\U0001f4c9",
        "extreme_fear": "\U0001f4a9",
    }
    return emojis.get(health, "\u26aa")


def _sentiment_arrow(sentiment: str) -> str:
    arrows = {
        "bullish": "\U0001f7e2",
        "bearish": "\U0001f534",
        "neutral": "\u26aa",
    }
    return arrows.get(sentiment, "\u26aa")


async def cmd_sentiment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show market sentiment from own data + external indicators.

    Usage: /sentiment
    Scout+ tier feature.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    tier = db.get_user_tier(user.id)
    if tier == "free" and not is_owner(user.id):
        await update.message.reply_text(
            "\U0001f4c8 <b>Market Sentiment</b>\n\n"
            "This feature requires <b>Scout tier</b> or higher.\n\n"
            "\U0001f50d <b>Scout ($29.99/mo)</b> — Unlock market intelligence",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return

    msg = await update.message.reply_text("\U0001f4c8 <b>Analyzing market sentiment...</b>", parse_mode=ParseMode.HTML)

    try:
        overview = await _fetch_market_overview()
        s = sep()
        d = thin_sep()
        health_emoji = _health_emoji(overview["market_health"])
        sent_arrow = _sentiment_arrow(overview["sentiment"])

        lines = [
            f"\U0001f4c8 <b>Market Sentiment</b>",
            s,
            f"{health_emoji} <b>Market Health:</b> {overview['market_health'].replace('_', ' ').title()}",
            f"{sent_arrow} <b>Sentiment:</b> {overview['sentiment'].title()}",
            "",
            "<b>\U0001f4ca Key Indicators</b>",
            d,
        ]

        fg = overview.get("fear_greed")
        if fg:
            fg_emoji = (
                "\U0001f525"
                if fg["value"] >= 75
                else "\U0001f4c8"
                if fg["value"] >= 60
                else "\u26aa"
                if fg["value"] >= 40
                else "\U0001f4c9"
                if fg["value"] >= 25
                else "\U0001f4a9"
            )
            lines.append(f"{fg_emoji} Fear & Greed Index: <b>{fg['value']}</b> — {fg['classification']}")

        btc_dom = overview.get("btc_dominance")
        if btc_dom is not None:
            change = overview.get("btc_dominance_change")
            change_str = f" ({change:+.1f}%)" if change else ""
            lines.append(f"\U0000243b BTC Dominance: <b>{btc_dom:.1f}%</b>{change_str}")

        total_mcap = overview.get("total_mcap")
        if total_mcap:
            lines.append(f"\U0001f4b0 Total Market Cap: <b>{fmt_number(total_mcap)}</b>")

        total_vol = overview.get("total_volume_24h")
        if total_vol:
            lines.append(f"\U0001f4e6 24h Volume: <b>{fmt_number(total_vol)}</b>")

        gas = overview.get("eth_gas")
        if gas:
            try:
                gas_val = int(gas)
                gas_emoji = "\U0001f534" if gas_val > 100 else "\U0001f7e1" if gas_val > 50 else "\U0001f7e2"
                lines.append(f"{gas_emoji} ETH Gas: <b>{gas_val} Gwei</b>")
            except (ValueError, TypeError):
                pass

        lines.extend(
            [
                "",
                "<b>\U0001f4a1 Interpretation</b>",
                d,
            ]
        )

        fg_val = fg["value"] if fg else 50
        if fg_val >= 75:
            lines.append("\U0001f525 <b>Extreme Greed</b> — Market may be overbought. Consider taking profits.")
        elif fg_val >= 60:
            lines.append("\U0001f4c8 <b>Greed</b> — Positive sentiment but stay cautious.")
        elif fg_val >= 40:
            lines.append("\u26aa <b>Neutral</b> — Balanced market. Good for DCA.")
        elif fg_val >= 25:
            lines.append("\U0001f4c9 <b>Fear</b> — Oversold conditions. Look for buying opportunities.")
        else:
            lines.append("\U0001f4a9 <b>Extreme Fear</b> — Maximum pessimism. Historical buying opportunity.")

        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)

        sentiment_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="sentiment_refresh")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=sentiment_kb,
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_sentiment_error", exc_info=True)
        logger.error(f"Sentiment error: {e}")
        await msg.edit_text(
            f"\u274c <b>Sentiment Unavailable</b>\n\nCould not fetch market sentiment right now.\nError: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
            ),
        )
