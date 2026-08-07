#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, paywall_kb, trace_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.trace")

EXCHANGE_LABELS = {
    "binance": "🏦 Binance",
    "coinbase": "🏦 Coinbase",
    "kraken": "🏦 Kraken",
    "bybit": "🏦 Bybit",
    "okx": "🏦 OKX",
    "gate": "🏦 Gate.io",
    "kucoin": "🏦 KuCoin",
    "mexc": "🏦 MEXC",
    "bitget": "🏦 Bitget",
    "huobi": "🏦 HTX",
    "unknown_exchange": "🏦 Exchange",
}


def _label_exchange(name: str) -> str:
    """Return emoji-labeled exchange name."""
    key = name.lower().replace(".", "").replace(" ", "_")
    for k, v in EXCHANGE_LABELS.items():
        if k in key or key in k:
            return v
    return f"🏦 {name}"


async def cmd_trace(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cross-chain fund flow tracing.

    Usage: /trace <address>
    Hunter tier and above.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
            "Usage: /trace <code>address</code>\n\n"
            "Traces fund flows across chains, identifies funding sources,\n"
            "exchange interactions, and connected wallets.\n\n"
            "<b>Example:</b>\n"
            "  /trace <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "🎯 <b>Hunter tier</b> or higher required.",
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            "🎯 <b>Hunter ($49.99/mo)</b> — Fund flow tracing\n"
            "👑 <b>Pro ($99.99/mo)</b> — Deep chain analytics + clustering",
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

    address = ctx.args[0]
    chain = ctx.args[1].lower() if len(ctx.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(
        f"🔍 <b>Starting fund trace...</b>\n<code>{short_addr(address)}</code>\n\n"
        f"⏳ Tracing across chains and connected wallets...",
        parse_mode=ParseMode.HTML,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            data: dict = {}
            # Fetch all available intelligence from backend
            try:
                wallet_resp = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
                    headers=backend_headers(),
                    timeout=15,
                )
                if wallet_resp.status_code == 200:
                    data = wallet_resp.json()
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.warning(f"trace wallet fetch failed: {e}")

            # Fetch additional cross-chain data
            cross_chain: list = []
            try:
                cc_resp = await client.get(
                    f"{BACKEND_URL}/api/v1/cross-chain/{address}",
                    params={"chain": chain},
                    headers=backend_headers(),
                    timeout=10,
                )
                if cc_resp.status_code == 200:
                    profile = cc_resp.json()
                    primary = profile.get("primary_chain", chain)
                    cross_chain = [
                        {
                            "source_chain": primary,
                            "dest_chain": m.get("chain", ""),
                            "address": m.get("address", ""),
                            "confidence": m.get("confidence", 0),
                        }
                        for m in profile.get("addresses", [])
                    ]
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                pass

            s = sep()
            d = thin_sep()

            lines = [
                "🔍 <b>Cross-Chain Fund Trace</b>",
                s,
                f"<code>{address}</code>",
                f"⛓️ Chain: {chain}",
                "",
            ]

            # Funding Source
            funding = data.get("funding_source")
            if funding:
                lines.extend(
                    [
                        "<b>💰 Funding Source</b>",
                        d,
                        f"  {funding}",
                        "",
                    ]
                )
            elif data.get("is_exchange"):
                lines.extend(
                    [
                        "💰 <b>Funding Source</b>",
                        d,
                        "  🏦 Exchange-funded wallet",
                        "",
                    ]
                )
            else:
                # Try to trace back first funder
                lines.extend(
                    [
                        "💰 <b>Funding Source</b>",
                        d,
                        "  ❓ Unknown — wallet may be self-funded or untraceable",
                        "",
                    ]
                )

            # Connected Wallets
            connected = data.get("connected_wallets", [])
            if connected:
                lines.extend(
                    [
                        "🔗 <b>Connected Wallets</b>",
                        d,
                    ]
                )
                for _i, w in enumerate(connected[:8], 1):
                    label = ""
                    if isinstance(w, dict):
                        addr_w = w.get("address", str(w))
                        label = w.get("label", "")
                        chain_w = w.get("chain", "")
                        conn_type = w.get("type", "linked")
                    else:
                        addr_w = str(w)
                        chain_w = ""
                        conn_type = "linked"

                    conn_emoji = {
                        "funded": "⬅️",
                        "funded_by": "➡️",
                        "linked": "🔗",
                        "same_owner": "👤",
                        "exchange": "🏦",
                        "contract": "📄",
                    }.get(conn_type, "🔗")
                    chain_tag = f" ({chain_w})" if chain_w else ""
                    label_tag = f" — {label}" if label else ""
                    lines.append(f"  {conn_emoji} <code>{short_addr(addr_w)}</code>{chain_tag}{label_tag}")
                if len(connected) > 8:
                    lines.append(f"  ... and {len(connected) - 8} more")
                lines.append("")

            # Cross-Chain Connections
            if cross_chain:
                lines.extend(
                    [
                        "🌉 <b>Cross-Chain Activity</b>",
                        d,
                    ]
                )
                for conn in cross_chain[:6]:
                    src = conn.get("source_chain", "")
                    dst = conn.get("dest_chain", "")
                    amt = conn.get("amount_usd", 0)
                    ts = conn.get("timestamp", "")
                    lines.append(
                        f"  🌐 {src} → {dst}"
                        f"{' ($' + fmt_number(amt) + ')' if amt else ''}"
                        f"{' | ' + ts[:10] if ts else ''}"
                    )
                if len(cross_chain) > 6:
                    lines.append(f"  ... and {len(cross_chain) - 6} more")
                lines.append("")

            # Exchange Interactions
            exchanges = data.get("exchange_interactions", data.get("exchanges", []))
            if exchanges:
                lines.extend(
                    [
                        "🏦 <b>Exchange Interactions</b>",
                        d,
                    ]
                )
                for ex in exchanges[:5]:
                    if isinstance(ex, dict):
                        name = ex.get("name", str(ex))
                        direction = ex.get("direction", "interacted")
                        amount = ex.get("amount_usd", 0)
                    else:
                        name = str(ex)
                        direction = "interacted"
                        amount = 0
                    direction_emoji = {
                        "deposit": "⬆️",
                        "withdrawal": "⬇️",
                        "interacted": "🔄",
                    }.get(direction.lower(), "🔄")
                    amt_str = f" ${fmt_number(amount)}" if amount else ""
                    lines.append(f"  {direction_emoji} {_label_exchange(name)}{amt_str}")
                lines.append("")

            # Wallet Activity Summary
            tx_count = data.get("tx_count", 0)
            balance = data.get("balance_usd", 0)
            age_days = data.get("wallet_age_days", 0)
            pnl = data.get("pnl_estimate")

            lines.extend(
                [
                    "<b>📊 Wallet Summary</b>",
                    d,
                    f"  💰 Balance: <b>{fmt_number(balance)}</b>",
                    f"  📝 Transactions: <b>{tx_count:,}</b>" if tx_count else "  📝 Transactions: N/A",
                    f"  ⏳ Wallet Age: <b>{age_days} days</b>" if age_days else "  ⏳ Wallet Age: Unknown",
                ]
            )

            if pnl is not None:
                pnl_emoji = "📈" if pnl >= 0 else "📉"
                lines.append(f"  {pnl_emoji} Est. P&L: <b>{fmt_number(pnl)}</b>")

            if data.get("flags"):
                lines.extend(["", "⚠️ <b>Flags</b>", d])
                for flag in data["flags"][:5]:
                    lines.append(f"  🔴 {flag}")

            lines.append(footer_links())

        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, address, chain, "trace", 0)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=trace_kb(address, chain),
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_trace_error", exc_info=True)
        logger.error(f"Trace error: {e}")
        await msg.edit_text(
            f"❌ <b>Trace Failed</b>\n\n"
            f"Could not trace funds for <code>{short_addr(address)}</code>.\n"
            f"Error: {str(e)[:150]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
