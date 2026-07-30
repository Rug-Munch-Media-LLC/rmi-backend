#!/usr/bin/env python3
"""
RugMunchBot -- /webhook command: Custom webhook management.

Pro tier. Users can set up webhooks to receive real-time data
about token events, price movements, wallet activity, etc.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import httpx
from telegram import Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, paywall_kb

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.webhook")

WEBHOOK_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

# Webhook event types users can subscribe to
WEBHOOK_EVENTS = {
    "price_change": "📊 Price change notifications",
    "whale_move": "🐋 Whale movement alerts",
    "token_deployed": "🆕 New token deployment",
    "liquidity_change": "💧 Liquidity change alerts",
    "first_swap": "🔄 First swap on new token",
    "wallet_activity": "👛 Wallet activity alerts",
}

WEBHOOK_EVENT_EMOJIS = {
    "price_change": "📊",
    "whale_move": "🐋",
    "token_deployed": "🆕",
    "liquidity_change": "💧",
    "first_swap": "🔄",
    "wallet_activity": "👛",
}


async def cmd_webhook(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set up and manage custom webhooks.

    Usage: /webhook <action> [url] [event_type]
    Actions: add, list, remove, test

    Pro tier only.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    tier = db.get_user_tier(user.id)
    if tier != "pro" and not is_owner(user.id):
        await update.message.reply_text(
            "🔗 <b>Custom Webhooks</b>\n\n"
            "Webhooks let you receive real-time data pushes to your own server.\n\n"
            "• Token price changes\n"
            "• Whale movements\n"
            "• New token deployments\n"
            "• Liquidity events\n\n"
            "👑 <b>Pro tier required</b> — $99.99/mo\n\n"
            "Upgrade now to unlock custom webhooks!",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    # Parse command
    action = ctx.args[0].lower() if ctx.args else "list"

    if action == "list":
        await _list_webhooks(update, ctx, user.id)
    elif action == "add":
        await _add_webhook(update, ctx, user.id)
    elif action == "remove":
        await _remove_webhook(update, ctx, user.id)
    elif action == "test":
        await _test_webhook(update, ctx, user.id)
    elif action == "events":
        await _list_events(update, user.id)
    else:
        await update.message.reply_text(
            "🔗 <b>Webhook Management</b>\n\n"
            "Usage: /webhook <code>add</code> <url> [event_type]\n"
            "       /webhook <code>list</code>\n"
            "       /webhook <code>remove</code> <webhook_id>\n"
            "       /webhook <code>test</code> [webhook_id]\n"
            "       /webhook <code>events</code>\n\n"
            "<b>Examples:</b>\n"
            "  /webhook add https://mybot.com/hook price_change\n"
            "  /webhook add https://mybot.com/hook whale_move\n"
            "  /webhook list\n"
            "  /webhook remove 1\n\n"
            "<b>Available events:</b>\n"
            + "\n".join(
                f"  {emoji} <code>{evt}</code> — {desc}"
                for evt, desc in WEBHOOK_EVENTS.items()
                for emoji in [WEBHOOK_EVENT_EMOJIS.get(evt, "🔗")]
            )
            + "\n\n<i>Leave event_type blank to get all events.</i>",
            parse_mode=ParseMode.HTML,
        )


async def _list_events(update: Update, _user_id: int) -> None:
    """Show available webhook event types."""
    text = "🔗 <b>Available Webhook Events</b>\n" + sep() + "\n\nChoose the events you want to receive:\n\n"
    for evt, desc in WEBHOOK_EVENTS.items():
        emoji = WEBHOOK_EVENT_EMOJIS.get(evt, "🔗")
        text += f"  {emoji} <code>{evt}</code>\n     {desc}\n\n"
    text += footer_links()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def _list_webhooks(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Show user's active webhooks."""
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM webhooks WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "🔗 <b>Your Webhooks</b>\n\n"
            "No webhooks configured yet.\n\n"
            "Add one:\n"
            "  /webhook add <code>https://your-server.com/hook</code>\n\n"
            "See event types:\n"
            "  /webhook events",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    text = f"🔗 <b>Your Webhooks</b>\n{sep()}\n\n"
    for row in rows:
        wh = dict(row)
        event_emoji = WEBHOOK_EVENT_EMOJIS.get(wh.get("event_type", ""), "🔗")
        status = "✅ Active" if wh.get("is_active") else "❌ Inactive"
        last_seen = ""
        if wh.get("last_ping"):
            last_seen = f"\n  📡 Last ping: {wh['last_ping'][:19]}"
        text += (
            f"<b>#{wh['id']}</b> {event_emoji} {wh.get('event_type', 'all')}\n"
            f"  🔗 {wh['url'][:80]}\n"
            f"  {status}{last_seen}\n\n"
        )
    text += "Add new: /webhook add <code>url</code> [event]\nRemove: /webhook remove <code>id</code>"
    text += footer_links()

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def _add_webhook(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Register a new webhook URL."""
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "❌ Usage: /webhook add <code>url</code> [event_type]\n\n"
            "Example: /webhook add https://mybot.com/hook price_change",
            parse_mode=ParseMode.HTML,
        )
        return

    url = ctx.args[1]
    event_type = ctx.args[2].lower() if len(ctx.args) > 2 else None

    # Validate URL
    if not WEBHOOK_URL_RE.match(url):
        await update.message.reply_text(
            "❌ Invalid URL. Must be a valid http:// or https:// URL.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Validate event type
    if event_type and event_type not in WEBHOOK_EVENTS:
        await update.message.reply_text(
            f"❌ Invalid event type: <code>{event_type}</code>\n\n"
            f"Available: {', '.join(WEBHOOK_EVENTS.keys())}\n"
            f"See /webhook events for details.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check limit: Pro gets 10 webhooks
    conn = db.get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM webhooks WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()["c"]
    if count >= 10:
        conn.close()
        await update.message.reply_text(
            "❌ Maximum 10 active webhooks. Remove one first:\n  /webhook remove <code>id</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Register webhook
    try:
        # Register with backend
        webhook_data = {
            "url": url,
            "event_type": event_type or "all",
            "user_id": user_id,
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/v1/scanner/webhooks/register",
                json=webhook_data,
                timeout=10,
            )
            backend_id = None
            if resp.status_code == 200:
                backend_id = resp.json().get("id")

        # Store locally
        conn.execute(
            "INSERT INTO webhooks (user_id, url, event_type, backend_id, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (user_id, url, event_type or "all", backend_id, int(time.time())),
        )
        conn.commit()
        wh_id = conn.lastrowid
        conn.close()

        event_label = WEBHOOK_EVENTS.get(event_type, "All events") if event_type else "All events"
        await update.message.reply_text(
            f"✅ <b>Webhook Registered!</b>\n\n"
            f"ID: <b>#{wh_id}</b>\n"
            f"URL: <code>{url[:80]}</code>\n"
            f"Event: {event_label}\n\n"
            f"Test it: /webhook test {wh_id}\n"
            f"List all: /webhook list\n"
            f"Remove: /webhook remove {wh_id}",
            parse_mode=ParseMode.HTML,
        )

    except httpx.HTTPError as e:
        conn.close()
        logger.warning(f"webhook backend registration failed: {e}")
        await update.message.reply_text(
            f"✅ <b>Webhook Saved Locally</b>\n\n"
            f"URL: <code>{url[:80]}</code>\n"
            f"Event: {event_type or 'all'}\n\n"
            f"⚠️ Backend registration delayed — webhook will activate shortly.\n"
            f"Use /webhook list to check status.",
            parse_mode=ParseMode.HTML,
        )
    except (ValueError, KeyError, TypeError) as e:
        logger.debug("webhook_add_error", exc_info=True)
        logger.error(f"webhook add error: {e}")
        await update.message.reply_text(
            "❌ Failed to register webhook. Please try again.",
            parse_mode=ParseMode.HTML,
        )


async def _remove_webhook(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Remove a webhook by ID."""
    if len(ctx.args) < 2 or not ctx.args[1].isdigit():
        await update.message.reply_text(
            "❌ Usage: /webhook remove <code>id</code>\nFind IDs with: /webhook list",
            parse_mode=ParseMode.HTML,
        )
        return

    wh_id = int(ctx.args[1])

    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM webhooks WHERE id = ? AND user_id = ?",
        (wh_id, user_id),
    ).fetchone()

    if not row:
        conn.close()
        await update.message.reply_text(
            "❌ Webhook not found. Use /webhook list to see your webhooks.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Deactivate in backend
    backend_id = row["backend_id"]
    if backend_id:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.delete(
                    f"{BACKEND_URL}/api/v1/scanner/webhooks/{backend_id}",
                    timeout=5,
                )
        except (httpx.HTTPError, ConnectionError, TimeoutError):
            pass

    conn.execute("DELETE FROM webhooks WHERE id = ?", (wh_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ <b>Webhook #{wh_id} removed.</b>\n\nYou will no longer receive events at this URL.",
        parse_mode=ParseMode.HTML,
    )


async def _test_webhook(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Send a test payload to a webhook URL."""
    if len(ctx.args) >= 2 and ctx.args[1].isdigit():
        wh_id = int(ctx.args[1])
        conn = db.get_db()
        row = conn.execute(
            "SELECT * FROM webhooks WHERE id = ? AND user_id = ?",
            (wh_id, user_id),
        ).fetchone()
        conn.close()
        if not row:
            await update.message.reply_text(
                "❌ Webhook not found. Use /webhook list.",
                parse_mode=ParseMode.HTML,
            )
            return
        url = row["url"]
    elif len(ctx.args) >= 2:
        url = ctx.args[1]
    else:
        # Get first active webhook
        conn = db.get_db()
        row = conn.execute(
            "SELECT * FROM webhooks WHERE user_id = ? AND is_active = 1 LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        if not row:
            await update.message.reply_text(
                "❌ No active webhooks. Add one first:\n  /webhook add <code>url</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        url = row["url"]

    msg = await update.message.reply_text(
        f"📡 <b>Sending test ping...</b>\n<code>{url[:80]}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "event": "test",
                    "source": "rugmunchbot",
                    "data": {
                        "message": "This is a test webhook from RugMunch Intelligence.",
                        "timestamp": time.time(),
                    },
                },
                headers={"User-Agent": "RugMunchBot/1.0 Webhook Test"},
                timeout=10,
            )

            if resp.status_code < 500:
                await msg.edit_text(
                    f"✅ <b>Test Successful!</b>\n\n"
                    f"URL: <code>{url[:80]}</code>\n"
                    f"Status: <b>{resp.status_code}</b>\n"
                    f"Response: {resp.text[:200]}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await msg.edit_text(
                    f"⚠️ <b>Test Sent but Server Error</b>\n\n"
                    f"URL: <code>{url[:80]}</code>\n"
                    f"Status: <b>{resp.status_code}</b>\n"
                    f"Your server returned an error. Check your webhook endpoint.",
                    parse_mode=ParseMode.HTML,
                )

        # Update last_ping
        conn = db.get_db()
        conn.execute(
            "UPDATE webhooks SET last_ping = datetime('now') WHERE url = ? AND user_id = ?",
            (url, user_id),
        )
        conn.commit()
        conn.close()

    except httpx.HTTPError as e:
        await msg.edit_text(
            f"❌ <b>Test Failed</b>\n\n"
            f"URL: <code>{url[:80]}</code>\n\n"
            f"Could not reach your endpoint.\n"
            f"Error: {str(e)[:150]}",
            parse_mode=ParseMode.HTML,
        )
    except (ConnectionError, TimeoutError, OSError) as e:
        await msg.edit_text(
            f"❌ <b>Connection Failed</b>\n\nURL: <code>{url[:80]}</code>\n\nError: {str(e)[:150]}",
            parse_mode=ParseMode.HTML,
        )
