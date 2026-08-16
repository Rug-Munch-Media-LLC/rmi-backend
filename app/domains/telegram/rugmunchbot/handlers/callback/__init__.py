#!/usr/bin/env python3
"""
RugMunchBot -- Callback query handler.

Handles all inline keyboard button presses.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    BOT_USERNAME,
    BUY_STARS_PACKS,
    SCAM_SCHOOL_TOPICS,
    SCAN_PACKS,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    WEBSITE_URL,
    backend_headers,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
    thin_sep,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.handlers.callback._admin import (
    _deny_callback_attempt,
    _dispatch_admin_cb,
)
from app.domains.telegram.rugmunchbot.handlers.callback._inline import (
    _inline_market_data,
    _inline_news,
    _inline_sentiment,
    _inline_trending,
)
from app.domains.telegram.rugmunchbot.handlers.callback._run_cards import _RUN_CARDS
from app.domains.telegram.rugmunchbot.keyboards import (
    admin_kb,
    back_kb,
    buy_stars_kb,
    category_account_kb,
    category_ai_kb,
    category_alerts_kb,
    category_charts_kb,
    category_help_kb,
    category_intel_kb,
    category_market_kb,
    category_portfolio_kb,
    category_rugmaps_kb,
    category_scan_kb,
    category_security_kb,
    category_wallet_kb,
    pricing_kb,
    scamschool_kb,
    token_buttons,
    topup_kb,
    welcome_menu_kb,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data
    if data is None:
        return
    user = query.from_user
    db.get_or_create_user(user.id, user.username, user.first_name)

    if data == "menu_main":
        await query.edit_message_text(
            welcome_card_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_menu_kb(user.id),
            disable_web_page_preview=True,
        )
    elif data == "menu_cat_admin":
        # Owner-only admin sub-menu — visible via the welcome menu Admin button
        # (which itself is only rendered for the owner).
        if not is_owner(user.id):
            await query.answer("\U0001f512 Owner only.", show_alert=True)
            await _deny_callback_attempt(update, ctx, "menu_cat_admin")
            return
        await query.edit_message_text(
            "\U0001f6e1\ufe0f <b>Admin Panel</b>\n\n"
            "\U0001f4ca /admin \u2014 Dashboard & stats\n"
            "\U0001f4e2 /admin_broadcast \u2014 Send to all users\n"
            "\U0001f465 /admin_user \u2014 Look up a user\n"
            "\U0001f3af /admin_set_tier \u2014 Change user tier\n"
            "\U0001f6ab /admin_ban \u2014 Ban a user\n"
            "\U0001f4dd /admin_logs \u2014 Audit log\n"
            "\U0001f6e1\ufe0f /admin_toggle \u2014 Bot ON/OFF\n"
            "\U0001f4ac /admin_toggle_chat \u2014 Chat ON/OFF\n\n"
            "<i>Admin buttons stay pinned below the input box \u2014 "
            "tap any message and use them anytime.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data == "menu_cat_scan":
        await query.edit_message_text(
            "\U0001f50d <b>Scan</b>\n\n"
            "Security audits across 25+ chains \u2014 Quick Scan, full "
            "forensic Deep Scan, AEGIS 9-module analysis, rug checks, "
            "deployer fingerprint and hype/compare tools.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_scan_kb(),
        )
    elif data == "menu_cat_wallet":
        await query.edit_message_text(
            "\U0001f45b <b>Wallet</b>\n\n"
            "Wallet forensics, portfolio holdings, watchlist tracking and "
            "trade simulation \u2014 all wallet tools in one place. Price "
            "alerts live under the dedicated Alerts section.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_wallet_kb(),
        )
    elif data == "menu_cat_alerts":
        await query.edit_message_text(
            "\U0001f514 <b>Alerts</b>\n\n"
            "Price & activity alerts, watchlist tracking, whale movement "
            "alerts and custom webhook notifications \u2014 stay on top of "
            "your tokens without staring at charts.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_alerts_kb(),
        )
    elif data == "menu_cat_intel":
        await query.edit_message_text(
            "\U0001f9e0 <b>Intelligence</b>\n\n"
            "Market overview, trending, movers, news, sentiment, social "
            "signals, RAG deep research, deep answers and freestyle AI "
            "chat \u2014 knowledge on demand.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_intel_kb(),
        )
    elif data == "menu_cat_charts":
        await query.edit_message_text(
            "\U0001f4c9 <b>RugCharts</b>\n\n"
            "Price chart images, technical analysis and market data for any "
            "token. Bubble maps live under RugMaps.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_charts_kb(),
        )
    elif data == "menu_cat_rugmaps":
        await query.edit_message_text(
            "\U0001f5fa\ufe0f <b>RugMaps</b>\n\n"
            "Bubble-map images of wallet clusters, fund flows, dev wallets "
            "and smart money \u2014 plus top-holder concentration analysis.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_rugmaps_kb(),
        )
    elif data == "menu_cat_security":
        await query.edit_message_text(
            "\U0001f6e1\ufe0f <b>Security & Education</b>\n\n"
            "Rug pull checklists, scam education, deep investigations and "
            "reporting suspicious addresses to the Rug Munch team.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_security_kb(),
        )
    elif data == "menu_cat_market":
        await query.edit_message_text(
            "\U0001f4c8 <b>Market</b>\n\n"
            "Market overview, trending, movers, new launches, whale "
            "activity, charts, prices, TA, DEX liquidity, holders, smart "
            "money and fund tracing \u2014 real-time market data.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_market_kb(),
        )
    elif data == "menu_cat_ai":
        await query.edit_message_text(
            "\U0001f916 <b>AI Chat</b>\n\n"
            "Talk to me about tokens, wallets, markets, DeFi, crypto news and "
            "coding \u2014 powered by RAG + LLM.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_ai_kb(),
        )
    elif data == "menu_cat_chat":
        # Direct entry to AI chat — show welcome card with examples
        from app.domains.telegram.rugmunchbot.handlers.chat import CHAT_WELCOME

        await query.edit_message_text(
            CHAT_WELCOME,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_cat_portfolio":
        await query.edit_message_text(
            "\U0001f4c1 <b>Portfolio</b>\n\nTrack your wallet holdings, watchlist, alerts and trade simulations.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_portfolio_kb(),
        )
    elif data == "menu_cat_account":
        await query.edit_message_text(
            "\U0001f464 <b>Account</b>\n\nYour usage dashboard, subscription plans, referrals and scam education.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_account_kb(),
        )
    elif data == "menu_cat_help":
        await query.edit_message_text(
            "\u2139\ufe0f <b>Help & About</b>\n\n"
            "Full command reference, community links and the Rug Munch mission: "
            "make crypto safer for everyone.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_help_kb(),
        )
    elif data in ("open_channel", "open_group", "open_website"):
        url = {
            "open_channel": TELEGRAM_CHANNEL,
            "open_group": TELEGRAM_GROUP,
            "open_website": WEBSITE_URL,
        }[data]
        await query.edit_message_text(
            f'\U0001f310 Open: <a href="{url}">{url}</a>',
            parse_mode=ParseMode.HTML,
            reply_markup=category_help_kb(),
            disable_web_page_preview=True,
        )
    elif data.startswith("run_"):
        await _dispatch_run_cmd(update, ctx, data[4:])
    elif data.startswith("admin_"):
        await _dispatch_admin_cb(update, ctx, data)
    elif data == "menu_scan":
        await query.edit_message_text(
            "\U0001f50d <b>Scan a Token</b>\n\nSend me a contract address:\n<code>0x6982...1933</code>\n\nOr use: /scan <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_wallet":
        await query.edit_message_text(
            "\U0001f9db <b>Wallet Check</b>\n\nSend a wallet address or use:\n/wallet <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_ta":
        await query.edit_message_text(
            "\U0001f4ca <b>Technical Analysis</b>\n\nUsage: /ta <code>address</code>\n\nAnalyzes momentum, volume, buy/sell pressure.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_alerts":
        await query.edit_message_text(
            "\U0001f514 <b>Price Alerts</b>\n\nUse /alerts to manage.\n\n<i>Scout+ tiers only.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_account":
        tier = db.get_user_tier(user.id)
        tc = TIERS.get(tier, TIERS["free"])
        scans, ai = db.get_weekly_usage(user.id)
        u = db.get_user_stats(user.id)
        text = (
            f"\U0001f464 <b>Account</b>\n{thin_sep()}\n"
            f"Tier: {tc['emoji']} {tc['name']}\n"
            f"Scans: {scans}/{tc.get('scans_per_week', 25)}\n"
            f"AI: {ai}/{tc.get('ai_msgs_per_week', 15)}\n"
            f"Bonus Scans: {u.get('bonus_scans', 0)}\n"
            f"Bonus AI: {u.get('bonus_ai_msgs', 0)}\n"
            f"Stars: {u.get('stars_balance', 0)}\u2b50\n"
            f"Watchlist: {u.get('watchlist_count', 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("\u2b50 Upgrade", callback_data="menu_pricing"),
                        InlineKeyboardButton("\U0001f50b Top Up", callback_data="menu_topup"),
                    ],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            ),
        )
    elif data == "menu_pricing":
        await query.edit_message_text(
            "\u2b50 <b>Upgrade Plan</b>\n\nSelect a tier:",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
    elif data == "menu_topup":
        await query.edit_message_text(
            "\U0001f50b <b>Top Up</b>\n\nBuy extra usage (no expiry):",
            parse_mode=ParseMode.HTML,
            reply_markup=topup_kb(),
        )
    elif data == "menu_scamschool":
        await query.edit_message_text(
            "\U0001f4da <b>Scam School</b>\n\nSelect a topic:",
            parse_mode=ParseMode.HTML,
            reply_markup=scamschool_kb(),
        )
    elif data == "menu_trending":
        await query.edit_message_text("\U0001f525 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import render_trending

        trend_text, trend_kb = await render_trending("solana", limit=8, window="24h")
        if trend_text is None or trend_kb is None:
            await query.edit_message_text(
                "\u274c Could not fetch trending data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        await query.edit_message_text(
            trend_text,
            parse_mode=ParseMode.HTML,
            reply_markup=trend_kb,
            disable_web_page_preview=True,
        )
    elif data == "trending_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f504 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import render_trending

        trend_text, trend_kb = await render_trending("solana", limit=8, window="24h")
        if trend_text is None or trend_kb is None:
            await query.edit_message_text(
                "\u274c Could not refresh trending data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        await query.edit_message_text(
            trend_text,
            parse_mode=ParseMode.HTML,
            reply_markup=trend_kb,
            disable_web_page_preview=True,
        )
    elif data == "news_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        articles = []
        source_label = "Backend"
        body = await http_get_json(
            f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
            headers=backend_headers(),
            timeout=10,
        )
        if body:
            articles = body.get("items", [])
        else:
            logger.warning("news_refresh backend failed")
        if not articles:
            from app.domains.telegram.rugmunchbot.handlers.market import (
                _fetch_cg_trending_news,
                _time_ago,
            )

            cg_items = await _fetch_cg_trending_news()
            if cg_items:
                articles = cg_items
                source_label = "Trending"
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
            news_kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                    [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            )
            import contextlib

            with contextlib.suppress(Exception):
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=news_kb,
                    disable_web_page_preview=True,
                )
        else:
            await query.edit_message_text(
                "\U0001f4f0 <b>Crypto News</b>\n\nNews feed temporarily unavailable.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("dismiss_alert_"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                "\u2705 Alert dismissed.",
                parse_mode=ParseMode.HTML,
            )
        except (ConnectionError, OSError, TimeoutError):
            pass
        await query.answer("Alert dismissed.")
    elif data == "menu_watchlist":
        items = db.get_watchlist(user.id)
        if not items:
            await query.edit_message_text(
                "\U0001f440 Watchlist is empty.\nUse /watch <code>address</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        else:
            text = "\U0001f440 <b>Watchlist</b>\n\n" + "\n".join(
                f"\u2022 {w.get('symbol', '???')} <code>{w['token_address']}</code>" for w in items
            )
            rows: list[list[InlineKeyboardButton]] = []
            for w in items:
                rows.append(token_buttons(w["token_address"], w.get("chain") or "solana"))
            rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
    elif data == "noop":
        await query.answer()
    elif data == "menu_refer":
        u = db.get_or_create_user(user.id)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{u.get('referral_code', '')}"
        await query.edit_message_text(
            f"\U0001f91d <b>Refer Friends</b>\n\nShare your link:\n<code>{link}</code>\n\nBoth get 5 bonus scans!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data.startswith("scam_"):
        topic_key = data[5:]
        topic = SCAM_SCHOOL_TOPICS.get(topic_key)
        if topic:
            await query.edit_message_text(topic["content"], parse_mode=ParseMode.MARKDOWN, reply_markup=scamschool_kb())
    elif data.startswith("scan_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

            ctx.args = [addr, chain]
            await cmd_scan(update, ctx)
    elif data.startswith("share_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            share_text = (
                f"\U0001f50d <b>RMI Token Scan</b>\n\n"
                f"<code>{addr}</code> ({chain.title()})\n\n"
                f"Scan this token with Rug Munch Bot:\n"
                f"https://t.me/RugMunchBot?start=scan_{addr}_{chain}"
            )
            await query.edit_message_text(
                share_text,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("holders_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.holders import cmd_holders

            ctx.args = [addr, chain]
            await cmd_holders(update, ctx)

    elif data.startswith("trace_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.trace import cmd_trace

            ctx.args = [addr, chain]
            await cmd_trace(update, ctx)

    elif data.startswith("wallet_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_wallet

            ctx.args = [addr, chain]
            await cmd_wallet(update, ctx)

    elif data.startswith("chart_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f4c8 <b>Generating chart...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
            from app.domains.telegram.rugmunchbot.commands.chart import cmd_chart

            ctx.args = [addr, chain]
            await cmd_chart(update, ctx)

    elif data.startswith("rugmap_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f5fa\ufe0f <b>Generating RugMap...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
            from app.domains.telegram.rugmunchbot.commands.rugmap import cmd_rugmap

            ctx.args = [addr, chain]
            await cmd_rugmap(update, ctx)

    elif data.startswith("alert_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.alerts import cmd_alerts

            ctx.args = []
            await cmd_alerts(update, ctx)

    elif data.startswith("upgrade_"):
        tier_key = data[8:]
        if tier_key in TIERS:
            tier_info = TIERS[tier_key]
            await query.edit_message_text(
                f"{tier_info['emoji']} <b>{tier_info['name']} — ${tier_info['price_monthly']}/mo</b>\n\n"
                f"Unlock {tier_info['scans_per_week']} scans/week, "
                f"{tier_info['ai_msgs_per_week']} AI messages/week, and:\n"
                + "\n".join(f"• {f}" for f in tier_info["features"][:6])
                + "\n\nSubscribe now and get instant access!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(f"⭐ Subscribe {tier_info['name']}", callback_data=f"sub_{tier_key}")],
                        [InlineKeyboardButton("🔙 Plans", callback_data="menu_pricing")],
                    ]
                ),
            )

    elif data == "api_revoke":
        await query.edit_message_text(
            "🔄 Revoke all active API tokens?\n\nThis will immediately invalidate all tokens.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Yes, Revoke All", callback_data="api_revoke_confirm")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="menu_main")],
                ]
            ),
        )

    elif data == "api_revoke_confirm":
        conn = db.get_db()
        conn.execute(
            "UPDATE api_tokens SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user.id,),
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(
            "✅ All API tokens revoked.\n\nGenerate new ones with /api create.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

    elif data.startswith("watch_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            ok = db.add_watchlist(user.id, addr, chain)
            await query.answer("Added to watchlist!" if ok else "Could not add (limit reached?)", show_alert=True)
    elif data == "menu_buystars":
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        await query.edit_message_text(
            f"\u2b50 <b>Buy Stars</b>\n\n"
            f"Your balance: \u2b50{u.get('stars_balance', 0):,}\n\n"
            f"Purchase Stars and spend them on scans, subscriptions, "
            f"and premium features!\n\n"
            f"Select a pack below \U0001f447",
            parse_mode=ParseMode.HTML,
            reply_markup=buy_stars_kb(),
        )
    elif data.startswith("buystars_"):
        pack_key = data[9:]
        pack = BUY_STARS_PACKS.get(pack_key)
        if not pack:
            await query.answer("Invalid pack.", show_alert=True)
            return
        try:
            msg = query.message
            if msg is None:
                await query.answer("Message unavailable.", show_alert=True)
                return
            await ctx.bot.send_invoice(
                chat_id=msg.chat.id,
                title=str(pack["name"]),
                description=f"Purchase {pack['name']} for your RugMunch account.",
                payload=f"buystars_{pack_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(str(pack["name"]), int(pack["stars"]))],
            )
            await query.answer()
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Buy stars invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
    elif data.startswith("research_"):
        parts = data.split("_", 2)
        if len(parts) == 2:
            # Topic-based research suggestion
            topic_key = parts[1]
            topic_desc = {
                "honeypot": "honeypot token detection patterns",
                "bundling": "bundle buying detection techniques",
                "wash_trading": "wash trading detection methods",
                "rugpull": "common rug pull strategies",
                "smart_money": "smart money wallet tracking",
                "fresh_wallet": "fresh wallet detection risk assessment",
                "mev": "MEV attacks sandwich detection",
                "pump_dump": "pump and dump scheme detection",
                "liquidity": "liquidity analysis LP verification",
            }.get(topic_key, topic_key)
            await query.edit_message_text(
                "\U0001f50d <b>Research Topic Selected</b>\n\n"
                f"Use /research <code>{topic_desc}</code>\n\n"
                "Or type a custom query:\n"
                "/research <code>your question here</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        elif len(parts) >= 3:
            addr_s, _chain_s = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.research import cmd_research

            ctx.args = [addr_s]
            await cmd_research(update, ctx)
    elif data.startswith("sentiment_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            from app.domains.telegram.rugmunchbot.handlers.sentiment import cmd_sentiment

            ctx.args = []
            await cmd_sentiment(update, ctx)
    elif data.startswith("investigate_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_inv, chain_inv = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.osint import cmd_investigate

            ctx.args = [addr_inv, chain_inv]
            await cmd_investigate(update, ctx)
    elif data.startswith("ta_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_ta, chain_ta = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_ta

            ctx.args = [addr_ta, chain_ta]
            await cmd_ta(update, ctx)
    elif data.startswith("mev_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_mev, chain_mev = parts[1], parts[2]
            from app.domains.telegram.rugmunchbot.commands.premium import cmd_mev

            ctx.args = [addr_mev, chain_mev]
            await cmd_mev(update, ctx)
    elif data.startswith("news_refresh"):
        await query.edit_message_text("\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        # Import and re-run cmd_news
        from app.domains.telegram.rugmunchbot.handlers.market import cmd_news as _news

        ctx.args = []
        await _news(update, ctx)
    elif data.startswith("sentiment_refresh"):
        await query.edit_message_text("\U0001f4c8 <b>Refreshing sentiment...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.sentiment import cmd_sentiment as _sentiment

        ctx.args = []
        await _sentiment(update, ctx)
    elif data.startswith("trending_chain_"):
        # Switch chain: trending_chain_{chain}
        new_chain = data[len("trending_chain_") :]
        if new_chain in ("solana", "ethereum", "base", "bsc", "arbitrum"):
            await query.edit_message_text(
                f"\U0001f525 <b>Loading {new_chain.capitalize()} trending...</b>",
                parse_mode=ParseMode.HTML,
            )
            from app.domains.telegram.rugmunchbot.handlers.market import render_trending

            trend_text, trend_kb = await render_trending(new_chain, limit=8, window="24h")
            if trend_text is None or trend_kb is None:
                await query.edit_message_text(
                    "\u274c Could not fetch trending data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            await query.edit_message_text(
                trend_text,
                parse_mode=ParseMode.HTML,
                reply_markup=trend_kb,
                disable_web_page_preview=True,
            )
        else:
            await query.answer("Chain not supported.", show_alert=True)
    elif data.startswith("trending_win_"):
        parts = data.split("_", 3)
        if len(parts) == 4 and parts[3] in ("1h", "24h", "7d"):
            win_chain, win = parts[2], parts[3]
            await query.edit_message_text("\U0001f525 <b>Loading trending...</b>", parse_mode=ParseMode.HTML)
            from app.domains.telegram.rugmunchbot.handlers.market import render_trending

            trend_text, trend_kb = await render_trending(win_chain, limit=8, window=win)
            if trend_text is None or trend_kb is None:
                await query.edit_message_text(
                    "\u274c Could not fetch trending data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            await query.edit_message_text(
                trend_text,
                parse_mode=ParseMode.HTML,
                reply_markup=trend_kb,
                disable_web_page_preview=True,
            )
        else:
            await query.answer("Invalid request.", show_alert=True)
    elif data.startswith("trending_refresh"):
        await query.edit_message_text("\U0001f525 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import cmd_trending as _trending

        parts = data.split("_", 2)
        ctx.args = [parts[2]] if len(parts) == 3 and parts[2] else []
        await _trending(update, ctx)
    elif data.startswith("market_refresh"):
        await _inline_market_data(update, ctx, "market")
    elif data.startswith("movers_refresh"):
        await _inline_market_data(update, ctx, "movers")
    elif data.startswith("launches_refresh"):
        await _inline_market_data(update, ctx, "launches")
    elif data.startswith("whales_refresh"):
        await _inline_market_data(update, ctx, "whales")
    elif data.startswith("deep_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.answer("Invalid request.", show_alert=True)
            return
        addr, chain = parts[1], parts[2]
        token = secrets.token_hex(8)
        from app.domains.telegram.rugmunchbot.states import _pending_deep_scans

        _pending_deep_scans[token] = {
            "user_id": user.id,
            "address": addr,
            "chain": chain,
            "chat_id": query.message.chat.id if query.message is not None else 0,
            "message_id": query.message.message_id if query.message is not None else 0,
        }
        try:
            msg = query.message
            if msg is None:
                await query.answer("Message unavailable.", show_alert=True)
                return
            await ctx.bot.send_invoice(
                chat_id=msg.chat.id,
                title="\U0001f52c Deep Token Scan",
                description=f"Complete forensic analysis of {addr} on {chain}.",
                payload=f"deep_{token}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice("Deep Scan", int(SCAN_PACKS["deep_scan"]["stars"]))],
            )
            await query.answer()
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Deep scan invoice error: {e}")
            _pending_deep_scans.pop(token, None)
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
    elif data.startswith("sub_"):
        tier_key = data[4:]
        if tier_key == "free":
            await query.answer("Free tier is already active!", show_alert=True)
            return
        sub_tier = TIERS.get(tier_key)
        if not sub_tier:
            await query.answer("Invalid tier.", show_alert=True)
            return
        try:
            msg = query.message
            if msg is None:
                await query.answer("Message unavailable.", show_alert=True)
                return
            await ctx.bot.send_invoice(
                chat_id=msg.chat.id,
                title=f"{sub_tier['emoji']} {sub_tier['name']} - 30 Days",
                description=(
                    f"Unlock {sub_tier['name']} tier for 30 days.\n"
                    f"\u2022 {sub_tier['scans_per_week']} scans/week\n"
                    f"\u2022 {sub_tier['ai_msgs_per_week']} AI messages/week\n"
                    f"\u2022 All premium features"
                ),
                payload=f"sub_{tier_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(f"{sub_tier['name']} - 30 days", sub_tier["price_stars"])],
            )
            await query.answer()
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Sub invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
    elif data.startswith("topup_"):
        pack_key = data[6:]
        pack = TOP_UP_PACKS.get(pack_key)
        if not pack:
            await query.answer("Invalid pack.", show_alert=True)
            return
        try:
            msg = query.message
            if msg is None:
                await query.answer("Message unavailable.", show_alert=True)
                return
            await ctx.bot.send_invoice(
                chat_id=msg.chat.id,
                title=str(pack["name"]),
                description=f"Purchase {pack['name']} \u2014 never expires!",
                payload=f"topup_{pack_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(str(pack["name"]), int(pack["stars"]))],
            )
            await query.answer()
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Topup invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )


# ══════════════════════════════════════════════
# v5 — run_* command cards dispatch
# ══════════════════════════════════════════════


async def _dispatch_run_cmd(update: Update, ctx, cmd_arg: str | None = None) -> None:
    """Handle run_* menu buttons: live inline data for trending/news/sentiment/
    market, real command execution for scan/price/chart, usage cards otherwise.

    ``cmd_arg`` is the raw callback payload after the ``run_`` prefix (from
    the callback handler). It is optional — when omitted (defensive) the
    command is re-derived from ``query.data``.
    """
    query = update.callback_query
    if query is None:
        return
    cmd = (query.data or "")[4:]

    if cmd == "trending":
        await _inline_trending(update, ctx)
        return
    if cmd == "news":
        await _inline_news(update, ctx)
        return
    if cmd == "sentiment":
        await _inline_sentiment(update, ctx)
        return
    if cmd in ("market", "movers", "launches", "whales"):
        await _inline_market_data(update, ctx, cmd)
        return
    if cmd in ("scan", "price", "chart"):
        await _exec_live_command(update, ctx, cmd)
        return

    card = _RUN_CARDS.get(cmd)
    if card is None:
        card = f"<code>/{cmd}</code> \u2014 check /help for usage."
    await query.edit_message_text(
        card + footer_links(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def _exec_live_command(update: Update, ctx, cmd: str) -> None:
    """Execute a real command handler for arg-less run_* menu buttons.

    run_scan / run_price / run_chart carry no address payload, so we invoke
    the registered command with empty args — it renders its own usage/entry
    flow instead of a static card.
    """
    if cmd == "scan":
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        handler: Any = cmd_scan
    elif cmd == "price":
        from app.domains.telegram.rugmunchbot.handlers.price import cmd_price

        handler = cmd_price
    else:
        from app.domains.telegram.rugmunchbot.commands.chart import cmd_chart

        handler = cmd_chart
    ctx.args = []
    await handler(update, ctx)
