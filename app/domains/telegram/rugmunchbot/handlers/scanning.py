#!/usr/bin/env python3
"""
RugMunchBot -- Core scanning command handlers.

Commands: start, help, scan, wallet, ta, compare, quick, rugcheck
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    DEXSCREENER,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    WEBSITE_URL,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    _price_diff_pct,
    _risk_indicator,
    footer_links,
    format_scan_report,
    format_wallet_report,
    is_owner,
    paywall_text,
    sep,
    short_addr,
    social_proof_text,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, paywall_kb, scan_result_kb
from app.domains.telegram.rugmunchbot.scanner import analyze_wallet, scan_token
from app.domains.telegram.rugmunchbot.states import check_spam

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)

    # Referral handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if ref_code != u.get("referral_code") and not u.get("referred_by"):
            conn = db.get_db()
            referrer = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET referred_by = ?, bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (ref_code, user.id),
                )
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (referrer["user_id"],),
                )
                conn.commit()
            conn.close()

    proof = social_proof_text()
    text = (
        f"\U0001f6e1\ufe0f <b>RugMunch Intelligence</b>\n"
        f"<i>The Bloomberg Terminal of Shitcoins</i>\n\n"
        f"{proof}\n\n"
        f"<b>What I can do for you:</b>\n\n"
        f"\U0001f50d <b>Token Scanning</b>\n"
        f"  Honeypots, rug pulls, bundles, fake volume\n"
        f"  across 77+ chains in seconds\n\n"
        f"\U0001f9db <b>Wallet Forensics</b>\n"
        f"  Track smart money, dev wallets, funding sources\n\n"
        f"\U0001f4ca <b>Market Intelligence</b>\n"
        f"  Technical analysis, trending tokens, alerts\n\n"
        f"\U0001f916 <b>AI-Powered Risk Scoring</b>\n"
        f"  Advanced detection that goes beyond surface-level\n\n"
        f"<b>Quick Start:</b>\n"
        f"  \U0001f4cc Paste any contract address \u2192 auto-scan\n"
        f"  \U0001f4cc Type <code>$TOKEN</code> \u2192 quick price lookup\n"
        f"  \U0001f4cc /scan <code>address</code> \u2192 full analysis\n"
        f"  \U0001f4cc /help \u2192 see all commands\n\n"
        f"\U0001f4f3 <b>Free:</b> {TIERS['free']['scans_per_week']} scans + {TIERS['free']['ai_msgs_per_week']} AI msgs/week\n"
        f"\u2b50 <b>Premium from $29.99/mo</b> - unlock everything"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f50d Scan a Token", callback_data="menu_scan")],
        [
            InlineKeyboardButton("\u2b50 View Premium Plans", callback_data="menu_pricing"),
            InlineKeyboardButton("\U0001f4da Learn About Scams", callback_data="menu_scamschool"),
        ],
        [
            InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL),
            InlineKeyboardButton("\U0001f4e2 Join Alerts", url=TELEGRAM_CHANNEL),
        ],
    ])
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = thin_sep()
    s = sep()
    text = (
        f"\U0001f4d6 <b>RugMunch Intelligence - Command Guide</b>\n"
        f"{s}\n\n"
        f"\U0001f50d <b>TOKEN ANALYSIS</b>\n{d}\n"
        f"/scan <code>address</code> - Full security scan\n"
        f"/ta <code>address</code> - Technical analysis & signals\n"
        f"/chart <code>address</code> - Price chart & trends\n"
        f"/simulate <code>address</code> [amount] - Buy/sell simulation\n"
        f"/compare <code>addr1</code> <code>addr2</code> - Side-by-side\n"
        f"/quick <code>symbol</code> - Quick price check (same as $TOKEN)\n\n"
        f"/price <code>address</code> [chain] - Token price by contract\n"
        f"/dex <code>address</code> [chain] - DEX pairs & liquidity\n"
        f"\U0001f9db <b>WALLET INTELLIGENCE</b>\n{d}\n"
        f"/wallet <code>address</code> - Forensic wallet analysis\n"
        f"/portfolio <code>address</code> - Wallet portfolio scanner\n"
        f"/smartmoney <code>address</code> - Smart money detection\n"
        f"/rugmap <code>address</code> - Wallet cluster visualization\n"
        f"/watch <code>address</code> [chain] - Add to watchlist\n"
        f"/unwatch <code>address</code> - Remove from watchlist\n"
        f"/watchlist - View your watchlist\n"
        f"/alerts - Price & activity alerts\n\n"
        f"\U0001f4ca <b>MARKET DATA</b>\n{d}\n"
        f"/trending - Hot tokens right now\n"
        f"/news - Latest crypto news\n\n"
        f"\U0001f4b3 <b>ACCOUNT & BILLING</b>\n{d}\n"
        f"/account - Dashboard & usage stats\n"
        f"/pricing - Subscription plans\n"
        f"/topup - Buy extra scans (no expiry)\n"
        f"/refer - Invite friends, earn scans\n\n"
        f"\U0001f4da <b>EDUCATION</b>\n{d}\n"
        f"/scamschool - Learn about crypto scams\n"
        f"/rugcheck - Quick rug pull checklist\n\n"
        f"\U0001f4a1 <b>PRO TIPS</b>\n{d}\n"
        f"\u2022 Paste any <code>0x...</code> address to auto-scan\n"
        f"\u2022 Type <code>$PEPE</code> for instant price check\n"
        f"\u2022 Use inline mode: <code>@RugMunchBot address</code> in any chat\n"
        f"\u2022 Upgrade for bundle detection, wallet forensics & more\n"
        f"\u2022 Use /refer to earn free scans"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL),
         InlineKeyboardButton("\U0001f4ac Support", url=TELEGRAM_GROUP)],
        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
    ])
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f50d <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "\U0001f4a1 <i>Or just paste a contract address directly!</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    msg = await update.message.reply_text(
        f"\U0001f50d <b>Scanning token...</b>\n"
        f"<code>{short_addr(target)}</code>\n\n"
        f"\u23f3 Querying DexScreener, GoPlus, Honeypot.is, RMI...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                f"\u274c <b>Token Not Found</b>\n\n"
                f"No data found for <code>{short_addr(target)}</code>.\n\n"
                f"\u2022 Check the address is correct\n"
                f"\u2022 Ensure the token has a trading pair\n"
                f"\u2022 Try specifying the chain: /scan <code>addr</code> solana",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        report = format_scan_report(result)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result["chain"]),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e :
        logger.debug("unknown_error", exc_info=True)
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            f"\u274c <b>Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f9db <b>Wallet Forensics</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "<b>Example:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "<i>Hunter+ tiers get full P&L, funding source tracing, and connected wallet analysis.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(
        "\U0001f9db <b>Analyzing wallet...</b>\n\u23f3 Querying on-chain data...", parse_mode=ParseMode.HTML
    )
    try:
        result = await analyze_wallet(addr, chain)
        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            report, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except (httpx.HTTPError) as e :
        logger.debug("cmd_wallet_error", exc_info=True)
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            f"\u274c <b>Wallet Analysis Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f4ca <b>Technical Analysis</b>\n\nUsage: /ta <code>address_or_symbol</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text(
        "\U0001f4ca <b>Running Technical Analysis...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{target}")
            if r.status_code != 200:
                await msg.edit_text("\u274c Token not found on DexScreener.", parse_mode=ParseMode.HTML)
                return
            pairs = r.json().get("pairs", [])
            if not pairs:
                await msg.edit_text("\u274c No trading pairs found.", parse_mode=ParseMode.HTML)
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "???")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            price = float(p.get("priceUsd", 0))
            pc = p.get("priceChange", {})
            vol = p.get("volume", {})
            txns = p.get("txns", {})
            signals = []
            h1 = pc.get("h1") or 0
            h24 = pc.get("h24") or 0
            d7 = pc.get("d7") or 0
            if h1 > 5 and h24 > 10:
                signals.append("\U0001f7e2 Strong short-term momentum")
            elif h1 < -5 and h24 < -10:
                signals.append("\U0001f534 Strong downtrend")
            if h24 > 0 > h1:
                signals.append("\U0001f7e1 Pullback in uptrend")
            if h24 < 0 < h1:
                signals.append("\U0001f7e1 Bounce in downtrend")
            v24 = float(vol.get("h24") or 0)
            v6 = float(vol.get("h6") or 0)
            if v6 > 0 and v24 > 0:
                vol_trend = v6 / (v24 / 4)
                if vol_trend > 1.5:
                    signals.append("\U0001f4c8 Volume increasing (acceleration)")
                elif vol_trend < 0.5:
                    signals.append("\U0001f4c9 Volume declining (loss of interest)")
            buys = txns.get("h24", {}).get("buys", 0)
            sells = txns.get("h24", {}).get("sells", 0)
            total_tx = buys + sells
            if total_tx > 0:
                buy_ratio = buys / total_tx
                if buy_ratio > 0.7:
                    signals.append(f"\U0001f7e2 Buy pressure dominant ({buy_ratio * 100:.0f}% buys)")
                elif buy_ratio < 0.3:
                    signals.append(f"\U0001f534 Sell pressure dominant ({(1 - buy_ratio) * 100:.0f}% sells)")
                else:
                    signals.append(f"\u26aa Balanced buy/sell ({buy_ratio * 100:.0f}% buys)")
            text = (
                f"\U0001f4ca <b>Technical Analysis</b>\n{sep()}\n"
                f"<b>{name}</b> ({symbol})\n<code>{target}</code>\n\n"
                f"\U0001f4b0 Price: <b>{fmt_number(price)}</b>\n"
                f"\U0001f504 1h: {fmt_pct(h1)} | 24h: {fmt_pct(h24)} | 7d: {fmt_pct(d7)}\n\n"
                f"\U0001f4c8 <b>Volume</b>\n{thin_sep()}\n"
                f"6h: {fmt_number(v6)} | 24h: {fmt_number(v24)}\n"
                f"Txns 24h: {total_tx:,} (B:{buys:,} S:{sells:,})\n\n"
                f"\U0001f3ed <b>Signals</b>\n{thin_sep()}\n"
            )
            if signals:
                text += "\n".join(signals)
            else:
                text += "\u26aa No strong signals detected"
            text += "\n\n<i>TA is not financial advice. Always /scan for security.</i>"
            text += footer_links()
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"TA error: {e}")
        await msg.edit_text(
            f"\u274c TA failed: {str(e)[:200]}", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )

async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "\u2696\ufe0f <b>Compare Tokens</b>\n\nUsage: /compare <code>addr1</code> <code>addr2</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    msg = await update.message.reply_text("\u2696\ufe0f <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML)
    try:
        r1, r2 = await asyncio.gather(scan_token(ctx.args[0]), scan_token(ctx.args[1]))
        risk1 = r1["risk_score"]
        risk2 = r2["risk_score"]
        better = []
        if risk1 < risk2:
            better.append(f"\u2705 <b>{r1['name']}</b> \u2014 lower risk ({risk1}% vs {risk2}%)")
        elif risk2 < risk1:
            better.append(f"\u2705 <b>{r2['name']}</b> \u2014 lower risk ({risk2}% vs {risk1}%)")
        else:
            better.append(f"\u26aa Both at {risk1}% risk")
        liq1 = r1["liquidity"] or 0
        liq2 = r2["liquidity"] or 0
        if liq1 > liq2 and liq1 > 0:
            better.append(f"\U0001f4a7 <b>{r1['name']}</b> \u2014 higher liquidity")
        elif liq2 > liq1 and liq2 > 0:
            better.append(f"\U0001f4a7 <b>{r2['name']}</b> \u2014 higher liquidity")
        price_diff = _price_diff_pct(r1["price"], r2["price"])

        def mini(r) -> str:
            risk_ind = _risk_indicator(r["risk_score"])
            return (
                f"<b>{r['name']}</b> ({r['symbol']})\n"
                f"  <code>{short_addr(r['address'])}</code>\n"
                f"  \U0001f4b0 Price: <b>{fmt_number(r['price'])}</b>\n"
                f"  \U0001f4c8 MCap: {fmt_number(r['mcap'])} | \U0001f4a7 Liq: {fmt_number(r['liquidity'])}\n"
                f"  \U0001f4ca Vol: {fmt_number(r['volume_24h'])} | 24h: {fmt_pct(r['price_change_24h'])}\n"
                f"  {risk_ind} Risk: <b>{r['risk_score']}%</b> | Threats: {len(r['threats'])}"
            )

        text = (
            f"\u2696\ufe0f <b>Token Comparison</b>\n{sep()}\n\n"
            f"<b>1\ufe0f\u20e3 Token A</b>\n{mini(r1)}\n\n"
            f"<b>2\ufe0f\u20e3 Token B</b>\n{mini(r2)}\n\n"
            f"<b>\U0001f4ca Price Difference:</b> {price_diff}\n\n"
            f"<b>\U0001f3c6 Verdict</b>\n{thin_sep()}\n" + "\n".join(better) + footer_links()
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except (httpx.HTTPError, KeyError, TypeError) as e :
        logger.debug("mini_error", exc_info=True)
        logger.error(f"cmd_compare failed: {e}")
        await msg.edit_text(
            "\u274c <b>Comparison Failed</b>\n\nPlease check both addresses and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick price check - same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(
            "\U0001fa99 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE", parse_mode=ParseMode.HTML
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
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
                    text = (
                        f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                        f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}")],
                        [web_scan_button(addr, chain)],
                    ])
                    await update.message.reply_text(
                        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True,
                    )
                    return
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"cmd_quick DexScreener lookup failed for symbol {symbol}: {e}")
    await update.message.reply_text(
        f"\u274c Could not find token: <b>{symbol}</b>", parse_mode=ParseMode.HTML
    )
