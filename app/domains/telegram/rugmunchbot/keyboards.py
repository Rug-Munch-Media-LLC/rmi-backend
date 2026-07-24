#!/usr/bin/env python3
"""
RugMunchBot -- Keyboard builders.

All inline keyboard markup generators in one place.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.rugmunchbot.config import (
    BUY_STARS_PACKS,
    SCAM_SCHOOL_TOPICS,
    SUPPORT_EMAIL,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    TWITTER_URL,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import dex_buttons, web_scan_button


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f50d Scan Token", callback_data="menu_scan"),
                InlineKeyboardButton("\U0001f9db Wallet Check", callback_data="menu_wallet"),
            ],
            [
                InlineKeyboardButton("\U0001f4ca Technical Analysis", callback_data="menu_ta"),
                InlineKeyboardButton("\U0001f525 Trending", callback_data="menu_trending"),
            ],
            [
                InlineKeyboardButton("\U0001f440 Watchlist", callback_data="menu_watchlist"),
                InlineKeyboardButton("\U0001f514 Alerts", callback_data="menu_alerts"),
            ],
            [
                InlineKeyboardButton("\U0001f4cb My Account", callback_data="menu_account"),
                InlineKeyboardButton("\u2b50 Upgrade", callback_data="menu_pricing"),
            ],
            [
                InlineKeyboardButton("\U0001f4da Scam School", callback_data="menu_scamschool"),
                InlineKeyboardButton("\U0001f91d Refer & Earn", callback_data="menu_refer"),
            ],
            [
                InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL),
            ],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
        ],
    ]
    dex_btns = dex_buttons(address, chain)
    for i in range(0, len(dex_btns), 2):
        rows.append(dex_btns[i : i + 2])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def pricing_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in ["scout", "hunter", "pro"]:
        t = TIERS[key]
        rows.append([
            InlineKeyboardButton(
                f"{t['emoji']} {t['name']} - ${t['price_monthly']}/mo",
                callback_data=f"sub_{key}",
            )
        ])
    rows.append([InlineKeyboardButton("\U0001f310 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")])
    rows.append([InlineKeyboardButton("\U0001f4e7 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton("\U0001f52c Scan Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "scans":
            rows.append([
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                    callback_data=f"topup_{key}",
                )
            ])
    rows.append([InlineKeyboardButton("\U0001f916 AI Message Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "ai_msgs":
            rows.append([
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                    callback_data=f"topup_{key}",
                )
            ])
    rows.append([InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def buy_stars_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in BUY_STARS_PACKS.items():
        rows.append([
            InlineKeyboardButton(
                f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                callback_data=f"buystars_{key}",
            )
        ])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def scamschool_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, topic in SCAM_SCHOOL_TOPICS.items():
        rows.append([InlineKeyboardButton(topic["title"], callback_data=f"scam_{key}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")]])


def social_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f310 Website", url=WEBSITE_URL),
                InlineKeyboardButton("\U0001d54f Twitter", url=TWITTER_URL),
            ],
            [
                InlineKeyboardButton("\U0001f4e2 Alerts", url=TELEGRAM_CHANNEL),
                InlineKeyboardButton("\U0001f4ac Chat", url=TELEGRAM_GROUP),
            ],
        ]
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\u2b50 View Plans", callback_data="menu_pricing")],
            [InlineKeyboardButton("\U0001f50b Buy Top-Up", callback_data="menu_topup")],
            [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
