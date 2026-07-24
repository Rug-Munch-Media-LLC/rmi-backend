#!/usr/bin/env python3
"""
RugMunchBot -- Owner/admin command handlers.
"""

import asyncio

from telegram import Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import TIERS
from app.domains.telegram.rugmunchbot.formatters import is_owner, thin_sep

logger = get_logger("rmi_bot")


async def cmd_admin_stats(update: Update, ctx) -> None:
    if not is_owner(update.effective_user.id):
        return
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    tiers = conn.execute("SELECT tier, COUNT(*) as c FROM users GROUP BY tier").fetchall()
    scans_today = (
        conn.execute(
            "SELECT SUM(scans) as c FROM weekly_usage WHERE week_start = ?",
            (db._current_week_start(),),
        ).fetchone()["c"]
        or 0
    )
    total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    tier_str = "\n".join(f"  {t['tier']}: {t['c']}" for t in tiers)
    await update.message.reply_text(
        f"\U0001f4ca <b>Bot Stats</b>\n{thin_sep()}\n"
        f"\U0001f465 Total Users: {total}\n\U0001f6ab Banned: {banned}\n\n"
        f"\U0001f4c8 <b>Tiers:</b>\n{tier_str}\n\n"
        f"\U0001f50d This Week: {scans_today}\n\U0001f50d All-Time: {total_scans}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_admin_set_tier(update: Update, ctx) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")
        return
    uid, tier = int(ctx.args[0]), ctx.args[1].lower()
    days = int(ctx.args[2]) if len(ctx.args) > 2 else 30
    if tier not in TIERS:
        await update.message.reply_text(f"Invalid tier. Options: {', '.join(TIERS.keys())}")
        return
    db.set_user_tier(uid, tier, days)
    await update.message.reply_text(f"\u2705 Set user {uid} to {tier} ({days} days)")


async def cmd_admin_broadcast(update: Update, ctx) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")
        return
    msg_text = " ".join(ctx.args).replace("\n", "\n")
    conn = db.get_db()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except (KeyError, TypeError) as e :
            logger.debug("cmd_admin_broadcast_error", exc_info=True)
            logger.debug(f"Broadcast failed for user {u['user_id']}: {e}")
            failed += 1
    await update.message.reply_text(f"\U0001f4e2 Broadcast complete.\n\u2705 Sent: {sent}\n\u274c Failed: {failed}")


async def cmd_admin_ban(update: Update, ctx) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_ban <user_id> [0|1]")
        return
    uid, state = int(ctx.args[0]), int(ctx.args[1]) if len(ctx.args) > 1 else 1
    conn = db.get_db()
    conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (state, uid))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"{'\U0001f6ab' if state else '\u2705'} User {uid} {'banned' if state else 'unbanned'}."
    )
