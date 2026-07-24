#!/usr/bin/env python3
"""
RugMunchBot -- Callback query handler.

Handles all inline keyboard button presses.
"""

import secrets

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    BOT_USERNAME,
    BUY_STARS_PACKS,
    DEXSCREENER,
    SCAM_SCHOOL_TOPICS,
    SCAN_PACKS,
    TELEGRAM_CHANNEL,
    TIERS,
    TOP_UP_PACKS,
    WEBSITE_URL,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    buy_stars_kb,
    main_menu_kb,
    pricing_kb,
    scamschool_kb,
    topup_kb,
)

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
            "\U0001f6e1\ufe0f <b>RugMunch Intelligence</b>\n\nWhat would you like to do?",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2b50 Upgrade", callback_data="menu_pricing"),
                 InlineKeyboardButton("\U0001f50b Top Up", callback_data="menu_topup")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]),
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
        await query.edit_message_text(
            "\U0001f525 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
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
                        lines.append(
                            f"\U0001f525 <b>{name}</b> ({sym}){boost_str}\n   <code>{short_addr(addr)}</code>"
                        )
                    await query.edit_message_text(
                        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_kb()
                    )
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
        await query.edit_message_text(
            "\U0001f504 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                boosts_r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
                if boosts_r.status_code == 200:
                    boost_list = boosts_r.json()
                    if isinstance(boost_list, list):
                        boost_list = boost_list[:6]
                        enriched = {}
                        addresses = {
                            b.get("tokenAddress", "") for b in boost_list if b.get("tokenAddress")
                        }
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
                        from app.domains.telegram.rugmunchbot.formatters import fmt_pct
                        refresh_kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("\U0001f504 Refresh", callback_data="trending_refresh")],
                            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                        ])
                        await query.edit_message_text(
                            "\n".join(lines),
                            parse_mode=ParseMode.HTML,
                            reply_markup=refresh_kb,
                            disable_web_page_preview=True,
                        )
                        return
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, telegram.error.TelegramError) as e:
            logger.warning(f"trending_refresh failed: {e}")
        await query.edit_message_text(
            "\u274c Could not refresh trending data.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "news_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text(
            "\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML
        )
        articles = []
        source_label = "Backend"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5")
                if r.status_code == 200:
                    body = r.json()
                    articles = body.get("items", [])
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("news_refresh backend failed: %s", e)
        if not articles:
            from app.domains.telegram.rugmunchbot.handlers.market import _fetch_cg_trending_news, _time_ago
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
            news_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ])
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
                "\u2705 Alert dismissed.", parse_mode=ParseMode.HTML,
            )
        except (ConnectionError, OSError, TimeoutError):  # noqa: BLE001
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
                f"\u2022 {w.get('symbol', '???')} <code>{short_addr(w['token_address'])}</code>"
                for w in items
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
            await query.edit_message_text(
                topic["content"], parse_mode=ParseMode.MARKDOWN, reply_markup=scamschool_kb()
            )
    elif data.startswith("scan_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            await query.edit_message_text(
                f"\U0001f50d Use /scan {parts[1]} to run a full scan.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("watch_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            ok = db.add_watchlist(user.id, addr, chain)
            await query.answer(
                "Added to watchlist!" if ok else "Could not add (limit reached?)", show_alert=True
            )
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
        except Exception as e:  # noqa: BLE001
            logger.error(f"Buy stars invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
            logger.error(f"Topup invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
