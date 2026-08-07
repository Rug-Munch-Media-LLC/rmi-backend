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
from telegram.error import TelegramError

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    BOT_USERNAME,
    BUY_STARS_PACKS,
    COINGECKO,
    DEXSCREENER,
    DEXSCREENER_TOKEN_BOOSTS,
    SCAM_SCHOOL_TOPICS,
    SCAN_PACKS,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    WEBSITE_URL,
    backend_headers,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
    short_addr,
    thin_sep,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    admin_kb,
    back_kb,
    buy_stars_kb,
    category_account_kb,
    category_ai_kb,
    category_help_kb,
    category_market_kb,
    category_portfolio_kb,
    category_scan_kb,
    pricing_kb,
    scamschool_kb,
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
            reply_markup=welcome_menu_kb(),
            disable_web_page_preview=True,
        )
    elif data == "menu_cat_scan":
        await query.edit_message_text(
            "\U0001f50d <b>Scan & Research</b>\n\n"
            "Deep token security audits, wallet forensics, deployer fingerprints "
            "and risk scores across 77+ chains.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_scan_kb(),
        )
    elif data == "menu_cat_market":
        await query.edit_message_text(
            "\U0001f4c8 <b>Market Intel</b>\n\n"
            "Prices, charts, trending tokens, sentiment, news and DEX liquidity "
            "\u2014 real-time market data.",
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
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(DEXSCREENER_TOKEN_BOOSTS)
                if r.status_code == 200:
                    boost_list = r.json()
                    if not isinstance(boost_list, list):
                        boost_list = []
                    lines = ["\U0001f525 <b>Trending</b>", thin_sep()]
                    for b in boost_list[:6]:
                        name = (b.get("name") or b.get("description", "???"))[:12]
                        sym = b.get("symbol", "???")
                        addr = b.get("tokenAddress", "")
                        boost_count = b.get("boostCount", 0)
                        boost_str = f" \U0001f525{boost_count}" if boost_count else ""
                        lines.append(f"\U0001f525 <b>{name}</b> ({sym}){boost_str}\n   <code>{short_addr(addr)}</code>")
                    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_kb())
                else:
                    await query.edit_message_text(
                        "\u274c Could not fetch trending data.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                    )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"handle_callback trending failed: {e}")
            await query.edit_message_text(
                "\u274c Trending unavailable. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data == "trending_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f504 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                boosts_r = await client.get(DEXSCREENER_TOKEN_BOOSTS)
                if boosts_r.status_code == 200:
                    boost_list = boosts_r.json()
                    if isinstance(boost_list, list):
                        boost_list = boost_list[:6]
                        enriched = {}
                        addresses = {b.get("tokenAddress", "") for b in boost_list if b.get("tokenAddress")}
                        for addr in addresses:
                            try:
                                tr = await client.get(f"{DEXSCREENER}/tokens/{addr}", timeout=4)
                                if tr.status_code == 200:
                                    pairs = tr.json().get("pairs", [])
                                    if pairs:
                                        enriched[addr] = pairs[0]
                            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                                pass
                        lines = ["\U0001f525 <b>Trending Tokens</b>", sep()]
                        for i, b in enumerate(boost_list, 1):
                            addr = b.get("tokenAddress", "")
                            name = (b.get("name") or b.get("description", "???"))[:12]
                            sym = b.get("symbol", "???")
                            chain = b.get("chainId", "?")
                            bc = b.get("boostCount", 0)
                            bs = f" \U0001f525{bc}" if bc else ""
                            pair = enriched.get(addr)
                            if pair:
                                price = float(pair.get("priceUsd", 0))
                                h24 = pair.get("priceChange", {}).get("h24") or 0
                                vol = float(pair.get("volume", {}).get("h24") or 0)
                                emoji = "\U0001f7e2" if h24 >= 0 else "\U0001f534"
                                lines.append(
                                    f"{i}. {emoji} <b>{name}</b> ({sym}) {chain}\n"
                                    f"   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}{bs}"
                                )
                            else:
                                lines.append(f"{i}. \U0001f525 <b>{name}</b> ({sym}) {chain}{bs}")
                        lines.append(footer_links())
                        refresh_kb = InlineKeyboardMarkup(
                            [
                                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="trending_refresh")],
                                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                            ]
                        )
                        await query.edit_message_text(
                            "\n".join(lines),
                            parse_mode=ParseMode.HTML,
                            reply_markup=refresh_kb,
                            disable_web_page_preview=True,
                        )
                        return
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
            TelegramError,
        ) as e:
            logger.warning(f"trending_refresh failed: {e}")
        await query.edit_message_text(
            "\u274c Could not refresh trending data.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
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
                source_label = "CoinGecko"
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
                f"\u2022 {w.get('symbol', '???')} <code>{short_addr(w['token_address'])}</code>" for w in items
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb())
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
    elif data.startswith("trending_refresh"):
        await query.edit_message_text("\U0001f525 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import cmd_trending as _trending

        ctx.args = []
        await _trending(update, ctx)
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
                description=f"Complete forensic analysis of {short_addr(addr)} on {chain}.",
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
        "dev history and liquidity across 77+ chains."
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
        "Usage: <code>/chart &lt;address&gt;</code>\n\n"
        "Historical chart, technical indicators and trend analysis."
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


async def _dispatch_run_cmd(update: Update, ctx) -> None:
    """Handle run_* menu buttons: live inline data for trending/news/sentiment,
    usage cards for everything else."""
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
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(DEXSCREENER_TOKEN_BOOSTS)
            if r.status_code == 200:
                boost_list = r.json()
                if not isinstance(boost_list, list):
                    boost_list = []
                lines = ["\U0001f525 <b>Trending Tokens</b>", thin_sep()]
                for i, b in enumerate(boost_list[:8], 1):
                    name = (b.get("name") or b.get("description", "???"))[:12]
                    sym = b.get("symbol", "???")
                    addr = b.get("tokenAddress", "")
                    boost_count = b.get("boostCount", 0)
                    boost_str = f" \U0001f525{boost_count}" if boost_count else ""
                    lines.append(
                        f"{i}. \U0001f525 <b>{name}</b> ({sym}){boost_str}\n   <code>{short_addr(addr)}</code>"
                    )
                lines.append(footer_links())
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton("\U0001f504 Refresh", callback_data="trending_refresh")],
                            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                        ]
                    ),
                    disable_web_page_preview=True,
                )
                return
        await query.edit_message_text(
            "\u274c Could not fetch trending data.", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"inline trending failed: {e}")
        await query.edit_message_text(
            "\u274c Trending unavailable. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


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
            source_label = "CoinGecko"
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
        async with httpx.AsyncClient(timeout=12) as client:
            lines = ["\U0001f4c8 <b>Market Sentiment</b>", thin_sep()]
            # Fear & Greed Index
            try:
                fg = await client.get("https://api.alternative.me/fng/?limit=1")
                if fg.status_code == 200:
                    fg_data = fg.json().get("data", [{}])[0]
                    value = int(fg_data.get("value", 50))
                    classification = fg_data.get("value_classification", "Neutral")
                    lines.append(f"\U0001f610 Fear & Greed: <b>{value}/100</b> ({classification})")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                logger.debug("fear&greed fetch failed")
            # CoinGecko global
            try:
                r = await client.get(f"{COINGECKO}/global")
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    mcap = data.get("total_market_cap", {}).get("usd")
                    if mcap:
                        lines.append(f"\U0001f4c8 Total Market Cap: <b>{fmt_number(mcap)}</b>")
                    btc_dom = data.get("market_cap_percentage", {}).get("btc")
                    if btc_dom:
                        lines.append(f"\U0001f4bf BTC Dominance: <b>{btc_dom:.1f}%</b>")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                logger.debug("coingecko global fetch failed")
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
