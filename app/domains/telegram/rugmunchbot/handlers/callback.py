#!/usr/bin/env python3
"""
RugMunchBot -- Callback query handler.

Handles all inline keyboard button presses.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    BOT_USERNAME,
    BUY_STARS_PACKS,
    COINGECKO,
    SCAM_SCHOOL_TOPICS,
    SCAN_PACKS,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    WEBSITE_URL,
    backend_headers,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.data_resolver import resolve_fear_greed, resolve_market_global
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
    thin_sep,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    admin_kb,
    back_kb,
    buy_stars_kb,
    category_account_kb,
    category_ai_kb,
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

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data
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
            "Security audits across 77+ chains \u2014 Quick Scan, full "
            "forensic Deep Scan, SENTINEL 9-module analysis, rug checks, "
            "deployer fingerprint and hype/compare tools.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_scan_kb(),
        )
    elif data == "menu_cat_wallet":
        await query.edit_message_text(
            "\U0001f45b <b>Wallet</b>\n\n"
            "Wallet forensics, portfolio holdings, watchlist tracking, "
            "price alerts and trade simulation \u2014 all wallet tools in "
            "one place.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_wallet_kb(),
        )
    elif data == "menu_cat_intel":
        await query.edit_message_text(
            "\U0001f9e0 <b>Intel</b>\n\n"
            "News, market sentiment, social signals, RAG deep research, "
            "deep answers and freestyle AI chat \u2014 knowledge on demand.",
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
            "reporting suspicious addresses to the RugMunch team.",
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
            "Full command reference, community links and the RugMunch mission: "
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

        text, kb = await render_trending("solana", limit=8, window="24h")
        if text is None or kb is None:
            await query.edit_message_text(
                "\u274c Could not fetch trending data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    elif data == "trending_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f504 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import render_trending

        text, kb = await render_trending("solana", limit=8, window="24h")
        if text is None or kb is None:
            await query.edit_message_text(
                "\u274c Could not refresh trending data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    elif data == "news_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        articles = []
        source_label = "Backend"
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
            logger.warning("news_refresh backend failed: %s", e)
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
            await query.edit_message_text(
                f"\U0001f50d Use /scan {parts[1]} to run a full scan.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("holders_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"👥 Use /holders {addr} {chain} to view holder analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )

    elif data.startswith("trace_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"🔍 Use /trace {addr} to trace cross-chain fund flows.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )

    elif data.startswith("wallet_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"👛 Use /wallet {addr} to analyze this wallet.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )

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
            await query.edit_message_text(
                f"🔔 To set a price alert:\n\n"
                f"  /alert ABOVE {addr} 0.01\n"
                f"  /alert BELOW {addr} 0.005\n"
                f"  /alert CHANGE_5 {addr}\n\n"
                f"Requires Scout+ tier.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )

    elif data.startswith("upgrade_"):
        tier_key = data[8:]
        if tier_key in TIERS:
            tier = TIERS[tier_key]
            await query.edit_message_text(
                f"{tier['emoji']} <b>{tier['name']} — ${tier['price_monthly']}/mo</b>\n\n"
                f"Unlock {tier['scans_per_week']} scans/week, "
                f"{tier['ai_msgs_per_week']} AI messages/week, and:\n"
                + "\n".join(f"• {f}" for f in tier["features"][:6])
                + "\n\nSubscribe now and get instant access!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(f"⭐ Subscribe {tier['name']}", callback_data=f"sub_{tier_key}")],
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
            await ctx.bot.send_invoice(
                chat_id=query.message.chat_id,
                title=pack["name"],
                description=f"Purchase {pack['name']} for your RugMunch account.",
                payload=f"buystars_{pack_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(pack["name"], pack["stars"])],
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
            await query.edit_message_text(
                "\U0001f50d Use /research or /investigate for deep analysis.\n\n"
                f"\U0001f50d /research <code>{addr_s}</code>\n"
                f"\U0001f575\ufe0f /investigate <code>{addr_s}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("sentiment_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            _addr_sent, _chain_sent = parts[1], parts[2]
            await query.edit_message_text(
                "\U0001f4c8 Use /sentiment to check market sentiment.\n\n"
                "Or use /ta for price-based technical analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("investigate_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_inv, _chain_inv = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f575\ufe0f Use /investigate <code>{addr_inv}</code> for deep investigation.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("ta_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_ta, _chain_ta = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f4ca Use /ta <code>{addr_ta}</code> for technical analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
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
    elif data.startswith("trending_win_"):
        parts = data.split("_", 3)
        if len(parts) == 4 and parts[3] in ("1h", "24h", "7d"):
            win_chain, win = parts[2], parts[3]
            await query.edit_message_text("\U0001f525 <b>Loading trending...</b>", parse_mode=ParseMode.HTML)
            from app.domains.telegram.rugmunchbot.handlers.market import render_trending

            text, kb = await render_trending(win_chain, limit=8, window=win)
            if text is None or kb is None:
                await query.edit_message_text(
                    "\u274c Could not fetch trending data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
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
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
        }
        try:
            await ctx.bot.send_invoice(
                chat_id=query.message.chat_id,
                title="\U0001f52c Deep Token Scan",
                description=f"Complete forensic analysis of {addr} on {chain}.",
                payload=f"deep_{token}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice("Deep Scan", SCAN_PACKS["deep_scan"]["stars"])],
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
        tier = TIERS.get(tier_key)
        if not tier:
            await query.answer("Invalid tier.", show_alert=True)
            return
        try:
            await ctx.bot.send_invoice(
                chat_id=query.message.chat_id,
                title=f"{tier['emoji']} {tier['name']} - 30 Days",
                description=(
                    f"Unlock {tier['name']} tier for 30 days.\n"
                    f"\u2022 {tier['scans_per_week']} scans/week\n"
                    f"\u2022 {tier['ai_msgs_per_week']} AI messages/week\n"
                    f"\u2022 All premium features"
                ),
                payload=f"sub_{tier_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(f"{tier['name']} - 30 days", tier["price_stars"])],
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
            await ctx.bot.send_invoice(
                chat_id=query.message.chat_id,
                title=pack["name"],
                description=f"Purchase {pack['name']} \u2014 never expires!",
                payload=f"topup_{pack_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(pack["name"], pack["stars"])],
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
# v5 — run_* command cards + admin_* callback dispatch
# ══════════════════════════════════════════════

# Usage cards shown when a user taps a command button that needs arguments.
_RUN_CARDS: dict[str, str] = {
    "scan": (
        "\U0001f6e1\ufe0f <b>Token Security Scan</b>\n\n"
        "Usage: <code>/scan &lt;address&gt; [chain]</code>\n\n"
        "Or just paste any contract address \u2014 I auto-detect it!\n\n"
        "\U0001f4a1 Checks honeypots, rug pulls, bundles, fake volume, "
        "dev history and liquidity across 77+ chains.\n\n"
        "\U0001f52c Want more? Use <code>/deepscan &lt;address&gt;</code> "
        "for the full multi-phase forensic audit."
    ),
    "deepscan": (
        "\U0001f52c <b>Deep Token Scan</b>\n\n"
        "Usage: <code>/deepscan &lt;address&gt; [chain]</code>\n\n"
        "Runs ALL phases: market data \u2192 contract security \u2192 holder "
        "analysis \u2192 deployer fingerprint \u2192 funding analysis \u2192 "
        "AI risk verdict.\n\n"
        "\u23f3 Slower than /scan (up to ~2 min) but far more detailed."
    ),
    "sentinelscan": (
        "\U0001f6e1\ufe0f <b>SENTINEL Deep Scan</b>\n\n"
        "Usage: <code>/sentinelscan &lt;address&gt; [chain]</code>\n\n"
        "Runs the full 9-module forensic pipeline: holder HHI, LP lock, "
        "dev reputation, contract authority, proxy, bundle, wash trading "
        "and honeypot analysis.\n\n"
        "\u23f3 Slowest scan (\u2248 1-2 min) \u2014 most thorough."
    ),
    "market": (
        "\U0001f4ca <b>Market Overview</b>\n\n"
        "Global market cap, 24h volume, BTC/ETH dominance, fear & greed "
        "index and top-asset movers \u2014 refreshed live from the backend."
    ),
    "movers": ("\U0001f680 <b>24h Market Movers</b>\n\nTop gainers and losers across the market over the last 24h."),
    "launches": (
        "\U0001f195 <b>New Token Launches</b>\n\n"
        "Freshly launched tokens with bundler flags \u2014 always "
        "/scan before buying."
    ),
    "whales": (
        "\U0001f40b <b>Whale Activity</b>\n\nLarge buy/sell moves detected across Solana/Ethereum/Bitcoin pairs."
    ),
    "rugcheck": (
        "\U0001f6a8 <b>Rug Pull Checklist</b>\n\n"
        "Usage: <code>/rugcheck &lt;address&gt;</code>\n\n"
        "Quick red-flag checklist: LP lock, mint authority, holder "
        "concentration, taxes and deployer history."
    ),
    "portfolio": (
        "\U0001f4c1 <b>Portfolio</b>\n\n"
        "Usage: <code>/portfolio &lt;wallet_address&gt;</code>\n\n"
        "Scans a wallet's token holdings \u2014 balances, values and risk "
        "flags. Track favorites with <code>/watch &lt;address&gt;</code>."
    ),
    "watchlist": (
        "\U0001f440 <b>Watchlist</b>\n\n"
        "Usage: <code>/watch &lt;address&gt;</code> to add \u2022 "
        "<code>/watchlist</code> to view \u2022 <code>/unwatch &lt;address&gt;</code> to remove."
    ),
    "alerts": (
        "\U0001f514 <b>Price Alerts</b>\n\n"
        "Usage: <code>/alert ABOVE &lt;addr&gt; &lt;price&gt;</code> or "
        "<code>/alert BELOW &lt;addr&gt; &lt;price&gt;</code>. Manage with "
        "<code>/alerts</code> \u2014 <b>Scout+ tiers</b>."
    ),
    "compare": (
        "\u2696\ufe0f <b>Compare Tokens</b>\n\n"
        "Usage: <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code>\n\n"
        "Side-by-side risk score, liquidity, price and holder metrics in one view."
    ),
    "social": (
        "\U0001f4e3 <b>Social Signals</b>\n\n"
        "Gauge community buzz before you buy:\n"
        "\u2022 <code>/hype &lt;symbol&gt;</code> \u2014 0-100 hype score incl. social velocity\n"
        "\u2022 <code>/sentiment</code> \u2014 market-wide fear & greed\n"
        "\u2022 <code>/news</code> \u2014 headlines with sentiment & bias\n"
        "\u2022 <code>/trending</code> \u2014 what the crowd is piling into\n\n"
        "Pair social signals with a <code>/scan</code> for the full picture."
    ),
    "smartmoney": (
        "\U0001f9e0 <b>Smart Money Detection</b>\n\n"
        "Usage: <code>/smartmoney &lt;address&gt;</code>\n\n"
        "Flags wallets with profitable histories \u2014 snipers, KOLs and repeat winners."
    ),
    "trace": (
        "\U0001f50d <b>Fund Flow Trace</b>\n\n"
        "Usage: <code>/trace &lt;address&gt;</code>\n\n"
        "Cross-chain fund flow tracing \u2014 <b>Hunter+ tier</b>."
    ),
    "ta": (
        "\U0001f4ca <b>Technical Analysis</b>\n\n"
        "Usage: <code>/ta &lt;address&gt;</code>\n\n"
        "Momentum, volume trend and buy/sell pressure signals for any token."
    ),
    "help": (
        "\U0001f4d6 <b>Help & About</b>\n\n"
        "Use <code>/help</code> for the full command reference, or browse "
        "the categories from the main menu.\n\n"
        "Community: \U0001f4e2 Alerts channel \u00b7 \U0001f4ac Chat group \u00b7 "
        "\U0001f310 rugmunch.io"
    ),
    "quick": (
        "\U0001fa99 <b>Quick Price Check</b>\n\n"
        "Usage: <code>/quick &lt;symbol&gt;</code>\n\n"
        "Same as typing <code>$TOKEN</code> \u2014 instant price check."
    ),
    "wallet": (
        "\U0001f9db <b>Wallet Forensics</b>\n\n"
        "Usage: <code>/wallet &lt;address&gt;</code>\n\n"
        "Holdings, risk score, entity attribution, scam exposure, "
        "smart money overlap and funding sources."
    ),
    "deployer": (
        "\U0001f575\ufe0f <b>Deployer Fingerprint</b>\n\n"
        "Usage: <code>/deployer &lt;address&gt;</code>\n\n"
        "Check the deployer's history for past rugs and suspicious patterns."
    ),
    "hype": (
        "\U0001f525 <b>Hype Score</b>\n\n"
        "Usage: <code>/hype &lt;symbol or address&gt;</code>\n\n"
        "Quick 0-100 community hype score for any token."
    ),
    "investigate": (
        "\U0001f50e <b>Deep Investigation</b>\n\n"
        "Usage: <code>/investigate &lt;address&gt;</code>\n\n"
        "Full forensic investigation report \u2014 <b>Pro tier</b>."
    ),
    "price": (
        "\U0001f4b0 <b>Token Price</b>\n\n"
        "Usage: <code>/price &lt;address&gt; [chain]</code>\n\n"
        "Live price by contract address, or just type <code>$SYMBOL</code>."
    ),
    "chart": (
        "\U0001f4c9 <b>Price Chart</b>\n\n"
        "Usage: <code>/chart &lt;address&gt; [chain]</code>\n\n"
        "Generates a chart image (OHLCV), "
        "with technical indicators and trend analysis."
    ),
    "rugmap": (
        "\U0001f5fa\ufe0f <b>RugMap Bubble Map</b>\n\n"
        "Usage: <code>/rugmap &lt;address&gt; [chain]</code>\n\n"
        "Generates a bubble-map image of wallet clusters, fund flows, "
        "dev wallets and smart money."
    ),
    "report": (
        "\U0001f6ab <b>Report a Scam</b>\n\n"
        "Usage: <code>/report &lt;address&gt; [reason]</code>\n\n"
        "Flags a token address as a scam. Reports feed the shared "
        "denylist and go straight to the RugMunch team."
    ),
    "dex": (
        "\U0001f4e6 <b>DEX Pairs & Liquidity</b>\n\n"
        "Usage: <code>/dex &lt;address&gt; [chain]</code>\n\n"
        "Trading pairs, liquidity pools and DEX referral links."
    ),
    "holders": (
        "\U0001f465 <b>Holder Analysis</b>\n\n"
        "Usage: <code>/holders &lt;address&gt;</code>\n\n"
        "Top holders & concentration \u2014 <b>Scout+ tier</b>."
    ),
    "simulate": (
        "\U0001f4b5 <b>Trade Simulator</b>\n\n"
        "Usage: <code>/simulate &lt;address&gt; [amount]</code>\n\n"
        "Simulate a buy/sell and see estimated P&L."
    ),
    "ask": (
        "\U0001f50d <b>Deep AI Answers</b>\n\n"
        "Usage: <code>/ask &lt;question&gt;</code>\n\n"
        "RAG + LLM synthesis from the RMI intelligence base.\n\n"
        "Example: /ask <code>how do honeypots work</code>"
    ),
    "research": (
        "\U0001f9e0 <b>RAG Deep Research</b>\n\n"
        "Usage: <code>/research &lt;query&gt;</code>\n\n"
        "Dives into threat intel, scam patterns and agent memory \u2014 "
        "<b>Hunter+ tier</b>.\n\n"
        "Example: /research <code>wash trading detection</code>"
    ),
    "chat": (
        "\U0001f4ac <b>Freestyle AI Chat</b>\n\n"
        "Usage: <code>/chat &lt;question&gt;</code>\n\n"
        "Or just type any message \u2014 no command needed. I answer "
        "crypto, DeFi, wallet and coding questions with RAG context.\n\n"
        "Example: /chat <code>write a Solidity ERC-20 with a tax</code>"
    ),
}


async def _dispatch_run_cmd(update: Update, ctx, cmd_arg: str | None = None) -> None:
    """Handle run_* menu buttons: live inline data for trending/news/sentiment,
    usage cards for everything else.

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

    card = _RUN_CARDS.get(cmd)
    if card is None:
        card = f"<code>/{cmd}</code> \u2014 check /help for usage."
    await query.edit_message_text(
        card + footer_links(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def _inline_trending(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f525 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML)
    from app.domains.telegram.rugmunchbot.handlers.market import render_trending

    text, kb = await render_trending("solana", limit=8, window="24h")
    if text is None or kb is None:
        await query.edit_message_text(
            "\u274c Could not fetch trending data.", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def _inline_market_data(update: Update, ctx, kind: str) -> None:
    """Live inline market data for run_market/run_movers/run_launches/run_whales.

    Reuses the backend market-intel fetch helpers from market.py (the same
    wiring as the /market, /movers, /launches and /whales commands) so menu
    buttons show live data instead of usage cards.
    """
    from app.domains.telegram.rugmunchbot.handlers.market import (
        _change_emoji,
        _fetch_market_intel,
        _fmt_usd,
    )

    query = update.callback_query
    if query is None:
        return

    labels = {
        "market": ("\U0001f4ca", "Fetching market overview...", "market_refresh"),
        "movers": ("\U0001f680", "Fetching market movers...", "movers_refresh"),
        "launches": ("\U0001f195", "Fetching new launches...", "launches_refresh"),
        "whales": ("\U0001f40b", "Fetching whale activity...", "whales_refresh"),
    }
    emoji, busy_text, refresh_cb = labels[kind]
    await query.edit_message_text(f"{emoji} <b>{busy_text}</b>", parse_mode=ParseMode.HTML)

    if kind == "market":
        text = await _inline_market_overview(_fetch_market_intel, _fmt_usd)
        token_items: list[dict] = []
    elif kind == "movers":
        text = await _inline_movers_text(_fetch_market_intel, _fmt_usd, _change_emoji)
        token_items = []
    elif kind == "launches":
        text, token_items = await _inline_launches_text(_fetch_market_intel, _fmt_usd, _change_emoji)
    else:
        text, token_items = await _inline_whales_text(_fetch_market_intel, _fmt_usd)

    if text is None:
        await query.edit_message_text(
            f"\u274c Could not fetch {kind} data.", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return
    kb_rows: list[list[InlineKeyboardButton]] = []
    for item in token_items:
        addr = item.get("address") or item.get("token_address") or ""
        if addr:
            kb_rows.append(token_buttons(addr, item.get("chain") or "solana"))
    kb_rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data=refresh_cb)])
    kb_rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows),
        disable_web_page_preview=True,
    )


async def _inline_market_overview(fetch, fmt_usd) -> str | None:
    """Render the /market overview as text for inline callbacks."""
    import asyncio

    overview, macro = await asyncio.gather(
        fetch("/api/v1/market/overview"),
        fetch("/api/v1/market/macro"),
    )
    if not overview:
        return None

    from app.domains.telegram.rugmunchbot.config import fmt_pct

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(f"\U0001f310 Total Market Cap: <b>{fmt_usd(overview.get('total_market_cap'))}</b>")
    if overview.get("total_volume_24h") is not None:
        lines.append(f"\U0001f4c8 24h Volume: <b>{fmt_usd(overview.get('total_volume_24h'))}</b>")
    if overview.get("btc_dominance") is not None:
        lines.append(f"\u20bf BTC Dominance: <b>{float(overview['btc_dominance']):.1f}%</b>")
    if overview.get("eth_dominance") is not None:
        lines.append(f"\u27e0 ETH Dominance: <b>{float(overview['eth_dominance']):.1f}%</b>")
    active = overview.get("active_cryptocurrencies")
    if active:
        import contextlib

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
            lines.append(f"\U0001f4b0 <b>{asset}:</b> {fmt_usd(it.get('current_price'))}{change_txt}")

    lines.append(footer_links())
    return "\n".join(lines)


async def _inline_movers_text(fetch, fmt_usd, change_emoji) -> str | None:
    """Render the /movers report as text for inline callbacks."""
    from app.domains.telegram.rugmunchbot.config import fmt_pct

    data = await fetch("/api/v1/market/movers", params={"limit": 20})
    items = (data or {}).get("items") or []
    if not items:
        return None

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
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {fmt_usd(item.get('price'))} | Vol: {fmt_usd(item.get('volume_24h'))}"
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
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {fmt_usd(item.get('price'))} | Vol: {fmt_usd(item.get('volume_24h'))}"
        )
    if not losers:
        lines.append("<i>No losers in the top movers \u2014 market is green \U0001f7e2</i>")

    lines.append(footer_links())
    return "\n".join(lines)


async def _inline_launches_text(fetch, fmt_usd, change_emoji) -> tuple[str | None, list[dict]]:
    """Render the /launches report as text for inline callbacks.

    Returns ``(text, token_items)`` so callers can attach per-token buttons.
    """
    from app.domains.telegram.rugmunchbot.config import fmt_chain, fmt_pct

    data = await fetch("/api/v1/market/launches", params={"limit": 8})
    items = (data or {}).get("items") or []
    if not items:
        return None, []

    lines = ["\U0001f195 <b>New Token Launches</b>", sep()]
    for i, item in enumerate(items[:8], 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:28]
        change = item.get("change_24h") or 0
        chain_txt = fmt_chain(item.get("chain") or "solana")
        bundler = "\U0001f6a8 bundler" if item.get("bundler_detected") else ""
        addr = item.get("address") or ""
        lines.append(
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol}) {chain_txt}\n"
            f"   {fmt_usd(item.get('price'))} | <b>{fmt_pct(change)}</b> | Vol: {fmt_usd(item.get('volume_24h'))}"
            f"{f' | {bundler}' if bundler else ''}"
        )
        if addr:
            lines.append(f"   <code>{addr}</code>")
    lines.append("\n<i>Fresh launches \u2014 always /scan before buying.</i>")
    lines.append(footer_links())
    return "\n".join(lines), items[:8]


async def _inline_whales_text(fetch, fmt_usd) -> tuple[str | None, list[dict]]:
    """Render the /whales report as text for inline callbacks.

    Returns ``(text, token_items)`` so callers can attach per-token buttons.
    """
    from app.domains.telegram.rugmunchbot.config import fmt_chain

    data = await fetch("/api/v1/market/whales", params={"limit": 5})
    items = (data or {}).get("items") or []
    if not items:
        return None, []

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
            f"   {fmt_usd(item.get('value_usd'))}"
            f"{f' | <code>{addr}</code>' if addr else ''}"
        )
    lines.append("\n<i>Large moves detected across Solana/Ethereum/Bitcoin pairs.</i>")
    lines.append(footer_links())
    return "\n".join(lines), items[:5]


async def _inline_news(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)
    articles: list[dict] = []
    source_label = "Backend"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
                headers=backend_headers(),
            )
            if r.status_code == 200:
                articles = r.json().get("items", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("inline news backend failed: %s", e)
    if not articles:
        from app.domains.telegram.rugmunchbot.handlers.market import _fetch_cg_trending_news

        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "Trending"
    if not articles:
        await query.edit_message_text(
            "\U0001f4f0 <b>Crypto News</b>\n\nNews feed temporarily unavailable.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    lines = ["\U0001f4f0 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
    for a in articles[:5]:
        title = (a.get("title") or "Untitled")[:100]
        source = a.get("source", "News")
        url = a.get("url") or a.get("link") or ""
        title_linked = f'<a href="{url}">{title}</a>' if url else title
        lines.append(f"\U0001f4f0 {title_linked}\n  <i>{source}</i>")
    lines.append(footer_links())
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
        disable_web_page_preview=True,
    )


async def _inline_sentiment(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f4c8 <b>Fetching sentiment...</b>", parse_mode=ParseMode.HTML)
    try:
        lines = ["\U0001f4c8 <b>Market Sentiment</b>", thin_sep()]

        # Own data first: backend market intel (cached). Only when the backend
        # is unreachable do we fall back to alternative.me + CoinGecko direct.
        # DATA_SOURCE is logged by the resolver for reliance tracking.

        async def _external_fng() -> dict | None:
            async with httpx.AsyncClient(timeout=12) as client:
                fg = await client.get("https://api.alternative.me/fng/?limit=1")
                if fg.status_code == 200:
                    fg_data = fg.json().get("data", [{}])[0]
                    return {
                        "value": int(fg_data.get("value", 50)),
                        "classification": fg_data.get("value_classification", "Neutral"),
                    }
            return None

        fng = await resolve_fear_greed(_external_fng)
        if fng and fng.get("value"):
            lines.append(
                f"\U0001f610 Fear & Greed: <b>{fng.get('value')}/100</b> ({fng.get('classification', 'Neutral')})"
            )

        async def _external_global() -> dict | None:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(f"{COINGECKO}/global")
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    return {
                        "total_market_cap": data.get("total_market_cap", {}).get("usd"),
                        "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
                    }
            return None

        global_data = await resolve_market_global(_external_global)
        if global_data:
            mcap = global_data.get("total_market_cap")
            if mcap:
                lines.append(f"\U0001f4c8 Total Market Cap: <b>{fmt_number(mcap)}</b>")
            btc_dom = global_data.get("btc_dominance")
            if btc_dom:
                lines.append(f"\U0001f4bf BTC Dominance: <b>{btc_dom:.1f}%</b>")

        lines.append(footer_links())
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f504 Refresh", callback_data="sentiment_refresh")],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            ),
            disable_web_page_preview=True,
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"inline sentiment failed: {e}")
        await query.edit_message_text(
            "\u274c Sentiment unavailable. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _dispatch_admin_cb(update: Update, ctx, data: str) -> None:
    """Handle admin_* callbacks. Owner-only; every action is logged."""
    query = update.callback_query
    if query is None:
        return
    if not is_owner(query.from_user.id):
        await query.answer("\u26a0\ufe0f Owner only.", show_alert=True)
        return

    from app.domains.telegram.rugmunchbot.handlers.owner import (
        cb_broadcast_confirm,
        pending_broadcast,
    )

    owner_uid = query.from_user.id
    chat_id = query.message.chat_id

    if data == "admin_stats":
        await _admin_stats_card(query)
    elif data == "admin_logs":
        await _admin_logs_card(query)
    elif data == "admin_user_prompt":
        await query.edit_message_text(
            "\U0001f465 <b>User Lookup</b>\n\nUse <code>/admin_user &lt;user_id&gt;</code>\n\n"
            "Example: /admin_user 7075336557",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data == "admin_broadcast_prompt":
        await query.edit_message_text(
            "\U0001f4e2 <b>Broadcast</b>\n\nUse <code>/admin_broadcast &lt;message&gt;</code> "
            "\u2014 you'll confirm before it sends.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data in ("admin_toggle_bot", "admin_toggle_bot_on", "admin_toggle_bot_off"):
        new_state = data.split("_")[-1] if data != "admin_toggle_bot" else None
        current = db.get_bot_state("bot_enabled", "on")
        if new_state in ("on", "off"):
            db.set_bot_state("bot_enabled", new_state)
            db.log_admin_action(owner_uid, "admin_toggle_bot", f"{current}->{new_state}")
            await query.edit_message_text(
                f"\U0001f6e1\ufe0f Bot is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED (maintenance)'}</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
            )
        else:
            await query.edit_message_text(
                f"\U0001f6e1\ufe0f Bot is currently <b>{current.upper()}</b>. Choose:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_bot_on"),
                            InlineKeyboardButton("\U0001f6d1 Disable", callback_data="admin_toggle_bot_off"),
                        ],
                        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                    ]
                ),
            )
    elif data in ("admin_toggle_chat", "admin_toggle_chat_on", "admin_toggle_chat_off"):
        new_state = data.split("_")[-1] if data != "admin_toggle_chat" else None
        current = db.get_bot_state("chat_enabled", "on")
        if new_state in ("on", "off"):
            db.set_bot_state("chat_enabled", new_state)
            db.log_admin_action(owner_uid, "admin_toggle_chat", f"{current}->{new_state}")
            await query.edit_message_text(
                f"\U0001f4ac AI Chat is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED'}</b> "
                "(commands keep working).",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
            )
        else:
            await query.edit_message_text(
                f"\U0001f4ac AI Chat is currently <b>{current.upper()}</b>. Choose:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_chat_on"),
                            InlineKeyboardButton("\U0001f4f5 Disable", callback_data="admin_toggle_chat_off"),
                        ],
                        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                    ]
                ),
            )
    elif data == "admin_confirm_broadcast":
        if not pending_broadcast(owner_uid):
            await query.edit_message_text(
                "\u274c No pending broadcast.", parse_mode=ParseMode.HTML, reply_markup=admin_kb()
            )
            return
        await query.edit_message_text("\U0001f4e2 <b>Sending broadcast...</b>", parse_mode=ParseMode.HTML)
        await cb_broadcast_confirm(owner_uid, chat_id, ctx.bot)
    elif data == "admin_cancel":
        from app.domains.telegram.rugmunchbot.handlers.owner import _pending_broadcast

        _pending_broadcast.pop(owner_uid, None)
        await query.edit_message_text("\u274c Broadcast cancelled.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
    elif data.startswith("admin_user_ban_"):
        target = data[len("admin_user_ban_") :]
        try:
            target_id = int(target)
        except (ValueError, TypeError):
            await query.answer("Invalid user id.", show_alert=True)
            return
        ok = db.set_banned(target_id, True)
        db.log_admin_action(owner_uid, "admin_ban", str(target_id))
        await query.edit_message_text(
            f"\U0001f6ab User <code>{target_id}</code> {'banned.' if ok else 'not found.'}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data.startswith("admin_user_tier_"):
        target = data[len("admin_user_tier_") :]
        await query.edit_message_text(
            f"\U0001f3af <b>Set Tier for {target}</b>\n\n"
            f"Use <code>/admin_set_tier {target} &lt;tier&gt; [days]</code>\n\n"
            f"Tiers: {', '.join(TIERS.keys())}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    else:
        await query.answer("Unknown admin action.")


async def _admin_stats_card(query) -> None:
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    active_today = db.count_active_today()
    ds = db.get_daily_stats()
    text = (
        f"\U0001f4ca <b>Bot Stats</b>\n{thin_sep()}\n"
        f"\U0001f465 Users: <b>{total}</b> | Active today: <b>{active_today}</b>\n"
        f"\U0001f6ab Banned: <b>{banned}</b>\n\n"
        f"\U0001f4ca <b>Today (UTC)</b>\n{thin_sep()}\n"
        f"\U0001f4ac Messages: <b>{ds['messages']}</b>\n"
        f"\U0001f4d6 Commands: <b>{ds['commands']}</b>\n"
        f"\U0001f50d RAG queries: <b>{ds['rag_queries']}</b>\n"
        f"\U0001f916 AI tokens: <b>{ds['ai_tokens']:,}</b>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def _admin_logs_card(query) -> None:
    from datetime import UTC, datetime

    logs = db.get_admin_logs(limit=10)
    if not logs:
        await query.edit_message_text(
            "\U0001f4dd No admin actions logged yet.", parse_mode=ParseMode.HTML, reply_markup=admin_kb()
        )
        return
    lines = ["\U0001f4dd <b>Recent Admin Actions</b>", thin_sep()]
    for entry in logs:
        ts = datetime.fromtimestamp(entry.get("created_at", 0), tz=UTC).strftime("%m-%d %H:%M")
        details = entry.get("details") or ""
        lines.append(f"\u2022 <code>{ts}</code> <b>{entry.get('action')}</b> {details[:50]}")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_kb())
