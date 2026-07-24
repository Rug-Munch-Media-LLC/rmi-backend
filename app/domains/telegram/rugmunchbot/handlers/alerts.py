#!/usr/bin/env python3
"""
RugMunchBot -- Alert engine + /alert and /alerts commands.

Background price alert checker and alert management commands.
"""

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,
    DEXSCREENER,
    TIERS,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    fmt_chain,
    is_owner,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, pricing_kb
from app.domains.telegram.rugmunchbot.states import check_alert_rate_limit

logger = get_logger("rmi_bot")

# --- Alert engine ---

DIRECTION_LABELS = {
    "above": "\U0001f4c8 above",
    "below": "\U0001f4c9 below",
    "change_5": "\U0001f4ca 5% change",
    "any": "\U0001f504 significant change",
}


async def _fetch_prices_batch(addresses: list[str]) -> dict[str, float]:
    """Fetch current USD prices for a list of token addresses from DexScreener."""
    prices: dict[str, float] = {}
    if not addresses:
        return prices
    try:
        batch = ",".join(addresses)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{batch}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                for pair in pairs:
                    addr = pair.get("baseToken", {}).get("address", "").lower()
                    price = float(pair.get("priceUsd", 0))
                    if addr and price > 0 and addr not in prices:
                        prices[addr] = price
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Batch price fetch error: {e}")
    return prices


def _check_alert_condition(current_price: float, alert_price: float, direction: str) -> bool:
    if current_price <= 0 or alert_price <= 0:
        return False
    if direction == "above":
        return current_price >= alert_price
    elif direction == "below":
        return current_price <= alert_price
    elif direction == "change_5":
        return abs((current_price - alert_price) / alert_price) * 100 >= 5.0
    elif direction == "any":
        return abs((current_price - alert_price) / alert_price) * 100 >= 2.0
    return False


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: check all active price alerts and fire notifications."""
    logger.info("\U0001f514 Checking price alerts...")

    active_alerts = db.get_active_alerts()
    if not active_alerts:
        logger.info("No active alerts to check.")
        return

    logger.info(f"Found {len(active_alerts)} active alert(s) to check.")
    unique_addresses = list({a["token_address"].lower() for a in active_alerts})
    current_prices = await _fetch_prices_batch(unique_addresses)

    if not current_prices:
        logger.warning("No prices fetched from DexScreener, skipping alert check.")
        return

    triggered: list[dict] = []
    for alert in active_alerts:
        addr = alert["token_address"].lower()
        current_price = current_prices.get(addr, 0.0)
        if current_price <= 0:
            continue
        if _check_alert_condition(current_price, alert["alert_price"], alert["alert_direction"]):
            triggered.append(alert)

    if not triggered:
        logger.info("No alerts triggered.")
        return

    logger.info(f"{len(triggered)} alert(s) triggered, sending notifications...")
    sent_count = 0
    rate_limited = 0

    for alert in triggered:
        user_id = alert["user_id"]
        if not check_alert_rate_limit(user_id):
            rate_limited += 1
            continue

        symbol = alert.get("symbol") or "???"
        addr = alert["token_address"]
        current_price = current_prices.get(addr.lower(), 0.0)
        target_price = alert["alert_price"]
        direction = alert["alert_direction"]

        if target_price > 0:
            change_pct = abs((current_price - target_price) / target_price) * 100
        else:
            change_pct = 0.0

        direction_emoji = "\U0001f4c8" if direction == "above" else "\U0001f4c9" if direction == "below" else "\U0001f4ca"
        direction_label = DIRECTION_LABELS.get(direction, direction)

        text = (
            f"\U0001f514 <b>Price Alert Triggered!</b>\n{sep()}\n\n"
            f"<b>{symbol}</b>\n"
            f"<code>{short_addr(addr)}</code>\n\n"
            f"\U0001f4b0 <b>Current Price:</b> {fmt_number(current_price)}\n"
            f"\U0001f3af <b>Target Price:</b> {fmt_number(target_price)}\n"
            f"{direction_emoji} <b>Direction:</b> {direction_label}\n"
            f"\U0001f4ca <b>Change:</b> {change_pct:.1f}%\n"
            f"{footer_links()}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2705 Dismiss", callback_data=f"dismiss_alert_{alert['id']}")],
        ])

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            sent_count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to send alert to user {user_id}: {e}")
            continue

        db.mark_alert_triggered(alert["id"])

    logger.info(
        f"Alert check complete: {sent_count} sent, {rate_limited} rate-limited, "
        f"{len(triggered) - sent_count - rate_limited} failed"
    )


# --- Alert commands ---

async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """View active and triggered alerts."""
    user = update.effective_user
    if user is None:
        return
    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(
            f"\U0001f514 <b>Price Alerts</b>\n\n"
            f"Get notified when tokens hit your target price.\n\n"
            f"\u26a0\ufe0f <b>Requires Scout tier</b> or higher.\n"
            f"You're on {TIERS[tier]['emoji']} {TIERS[tier]['name']}.\n\n"
            f"\U0001f3af <b>Hunter+</b> gets real-time exchange flow alerts too!",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    items = db.get_watchlist_for_user(user.id, include_triggered=True)
    active_alerts = [w for w in items if w.get("alert_price") and w.get("is_active")]
    triggered_alerts = [
        w for w in items
        if w.get("alert_price") and not w.get("is_active") and w.get("alert_triggered_at")
    ]

    if not active_alerts and not triggered_alerts:
        await update.message.reply_text(
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "No alerts set yet.\n\n"
            "To set an alert:\n"
            "  /alert ABOVE <code>address</code> 0.005\n"
            "  /alert BELOW <code>address</code> 0.85\n"
            "  /alert CHANGE_5 <code>address</code>\n"
            "  /alert ANY <code>address</code> 0.01",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    text = f"\U0001f514 <b>Alerts</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"

    if active_alerts:
        text += "<b>\U0001f7e2 Active</b>\n"
        for a in active_alerts:
            arrow = "\u2191" if a.get("alert_direction") == "above" else "\u2193"
            text += f"  \u2022 <b>{a.get('symbol', '???')}</b> {arrow} {fmt_number(a['alert_price'])}\n"
        text += "\n"

    if triggered_alerts:
        text += "<b>\U0001f534 Triggered (recent)</b>\n"
        for a in triggered_alerts[:5]:
            ts_str = ""
            if a.get("alert_triggered_at"):
                try:
                    ts = a["alert_triggered_at"]
                    if "T" in ts:
                        ts_str = f" \u2014 {ts[:10]}"
                except Exception:  # noqa: BLE001
                    pass
            text += f"  \u2022 <b>{a.get('symbol', '???')}</b> \U0001f514 {fmt_number(a['alert_price'])}{ts_str}\n"
        if len(triggered_alerts) > 5:
            text += f"  ... and {len(triggered_alerts) - 5} more\n"

    text += "\nSet new: /alert ABOVE <code>addr</code> <price>"
    text += footer_links()
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a price alert: /alert <ABOVE|BELOW> <address> <price>"""
    user = update.effective_user
    if user is None:
        return

    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "Price alerts require <b>Scout tier</b> or higher.\n\n"
            "\U0001f50d <b>Scout ($29.99/mo)</b> \u2014 10 watchlist slots + alerts\n"
            "\U0001f3af <b>Hunter ($49.99/mo)</b> \u2014 50 slots + real-time alerts\n"
            "\U0001f451 <b>Pro ($99.99/mo)</b> \u2014 unlimited + custom rules",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    if len(ctx.args) < 2:
        await update.message.reply_text(
            "\U0001f514 <b>Set Price Alert</b>\n\n"
            "Usage: /alert <code>ABOVE|BELOW|CHANGE_5|ANY</code> <code>address</code> <price>\n\n"
            "<b>Examples:</b>\n"
            "  /alert ABOVE 0x6982508145454Ce325dDbE47a25d4ec3d2311933 0.005\n"
            "  /alert BELOW EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.85\n"
            "  /alert CHANGE_5 0x... \u2014 5% move in either direction\n"
            "  /alert ANY 0x... 0.01 \u2014 alert on 2%+ change\n\n"
            "<i>Add the token to your watchlist first with /watch</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    direction_raw = ctx.args[0].upper()
    addr = ctx.args[1]
    price_str = ctx.args[2] if len(ctx.args) > 2 else None

    valid_directions = {"ABOVE", "BELOW", "CHANGE_5", "ANY"}
    if direction_raw not in valid_directions:
        await update.message.reply_text(
            f"\u274c Invalid direction. Use one of: {', '.join(d.lower() for d in valid_directions)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if direction_raw in ("ABOVE", "BELOW") and not price_str:
        await update.message.reply_text(
            "\u274c Please provide a target price for ABOVE/BELOW alerts.\n"
            "Example: /alert ABOVE <code>address</code> 0.005",
            parse_mode=ParseMode.HTML,
        )
        return

    chain = detect_chain(addr)
    if chain == "unknown":
        await update.message.reply_text(
            "\u274c Could not detect chain. Ensure it's a valid EVM (0x...) or Solana address.",
            parse_mode=ParseMode.HTML,
        )
        return

    alert_price: float | None = None
    if price_str:
        try:
            alert_price = float(price_str)
            if alert_price <= 0:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(
                "\u274c Invalid price. Use a positive number (e.g., 0.005, 1.50, 42000).",
                parse_mode=ParseMode.HTML,
            )
            return

    existing = db.get_watchlist(user.id)
    existing_entry = next(
        (w for w in existing if w["token_address"].lower() == addr.lower()),
        None,
    )

    if existing_entry:
        conn = db.get_db()
        conn.execute(
            "UPDATE watchlists SET alert_price = ?, alert_direction = ?, is_active = 1, alert_triggered_at = NULL WHERE id = ?",
            (alert_price, direction_raw.lower(), existing_entry["id"]),
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"\u2705 <b>Alert Updated</b>\n\n"
            f"Token: <code>{short_addr(addr)}</code>\n"
            f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
            f"Use /alerts to see all your active alerts.",
            parse_mode=ParseMode.HTML,
        )
    else:
        ok = db.add_watchlist(user.id, addr, chain, symbol=None, alert_price=alert_price, alert_dir=direction_raw.lower())
        if ok:
            await update.message.reply_text(
                f"\u2705 <b>Alert Set</b>\n\n"
                f"Token: <code>{short_addr(addr)}</code>\n"
                f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
                f"You'll be notified when this condition is met.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "\u274c Could not set alert. Watchlist limit reached or already exists.",
                parse_mode=ParseMode.HTML,
            )
