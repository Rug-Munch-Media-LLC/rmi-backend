"""
RugMunchBot -- Price and DEX command handlers.

New commands:
  /price <address> [chain]    — Get token price (by contract address)
  /dex <address> [chain]      — Show DEX trading pairs & liquidity
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import DEXSCREENER, WEBSITE_URL
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    fmt_chain,
    fmt_number,
    fmt_pct,
    footer_links,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.scanner_bridge import ScannerBridge

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.price")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get token price by contract address.

    Usage: /price <address> [chain]
    Example: /price 0x6982508145454Ce325dDbE47a25d4ec3d2311933
             /price EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana
    """
    if not context.args:
        await update.message.reply_text(
            "💰 <b>Token Price</b>\n\n"
            "Usage: /price <code>address</code> [chain]\n\n"
            "Examples:\n"
            "  /price <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /price <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "💡 <i>Get price data directly by contract address, not symbol.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    address = context.args[0]
    chain = context.args[1].lower() if len(context.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(
        f"💰 <b>Fetching price...</b>\n<code>{short_addr(address)}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        bridge = ScannerBridge()
        result = await bridge.get_price(address, chain)

        if result["price_usd"] > 0:
            text = (
                f"💰 <b>Token Price</b>\n"
                f"{thin_sep()}\n"
                f"<code>{short_addr(address)}</code>\n"
                f"{fmt_chain(chain)}\n\n"
                f"💵 Price: <b>{fmt_number(result['price_usd'])}</b>\n"
                f"📈 24h Change: {fmt_pct(result.get('change_24h', 0))}\n"
                f"💧 Volume 24h: {fmt_number(result.get('volume_24h', 0))}\n\n"
                f"📡 Source: {result.get('source', 'unavailable')}"
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("📊 Charts & Analysis", url=f"{WEBSITE_URL}/markets/token/{address}")],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )
        else:
            text = (
                f"❌ <b>Price Not Available</b>\n\n"
                f"<code>{short_addr(address)}</code>\n\n"
                f"No price data found for this token. It may:\n"
                f"• Not have a trading pair yet\n"
                f"• Be on an unsupported chain\n"
                f"• Have no liquidity\n\n"
                f"Try /scan for a full security analysis."
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )

        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, OSError) as e:
        logger.warning("cmd_price failed for %s: %s", short_addr(address), e)
        await msg.edit_text(
            f"❌ <b>Price Check Failed</b>\n\n"
            f"Could not fetch price for <code>{short_addr(address)}</code>.\n"
            f"Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_dex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show DEX trading pairs and liquidity for a token.

    Usage: /dex <address> [chain]
    Example: /dex 0x6982508145454Ce325dDbE47a25d4ec3d2311933
    """
    if not context.args:
        await update.message.reply_text(
            "💱 <b>DEX Data</b>\n\n"
            "Usage: /dex <code>address</code> [chain]\n\n"
            "Shows trading pairs, liquidity, and DEX info for a token.\n\n"
            "Examples:\n"
            "  /dex <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /dex <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana",
            parse_mode=ParseMode.HTML,
        )
        return

    address = context.args[0]
    chain = context.args[1].lower() if len(context.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(
        f"💱 <b>Fetching DEX data...</b>\n<code>{short_addr(address)}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{DEXSCREENER}/tokens/{address}")

            if resp.status_code != 200:
                await msg.edit_text(
                    f"❌ <b>DEX Data Unavailable</b>\n\nDexScreener returned status {resp.status_code} for this token.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return

            data = resp.json()
            pairs = data.get("pairs", [])

            if not pairs:
                await msg.edit_text(
                    f"❌ <b>No Trading Pairs Found</b>\n\n"
                    f"No DEX pairs found for <code>{short_addr(address)}</code>.\n"
                    f"The token may not be tradable on any DEX yet.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return

            # Show top pairs (up to 5)
            lines = [
                f"💱 <b>DEX Pairs: {len(pairs)} found</b>",
                thin_sep(),
                f"<code>{short_addr(address)}</code>\n",
            ]

            for i, p in enumerate(pairs[:5], 1):
                dex_name = p.get("dexId", "?").capitalize()
                chain_id = p.get("chainId", chain)
                price = float(p.get("priceUsd", 0))
                liq = float(p.get("liquidity", {}).get("usd", 0))
                vol_24h = float(p.get("volume", {}).get("h24", 0))
                pair_addr = p.get("pairAddress", "")[:12]

                lines.append(
                    f"{i}. <b>{dex_name}</b> ({fmt_chain(chain_id)})\n"
                    f"   💵 {fmt_number(price)} | 💧 {fmt_number(liq)}\n"
                    f"   📊 Vol: {fmt_number(vol_24h)} | <code>{pair_addr}...</code>"
                )

            lines.append(f"\n🌐 <a href='{WEBSITE_URL}/markets/token/{address}'>Full market data on RugMunch.io</a>")
            lines.append(footer_links())

            text = "\n".join(lines)
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("🤖 AI Analysis", callback_data=f"ai_scan_{address}")],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )

            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError, OSError) as e:
        logger.warning("cmd_dex failed for %s: %s", short_addr(address), e)
        await msg.edit_text(
            f"❌ <b>DEX Query Failed</b>\n\n"
            f"Could not fetch DEX data for <code>{short_addr(address)}</code>.\n"
            f"Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
