#!/usr/bin/env python3
"""
RugMunchBot -- Keyboard builders.

All inline keyboard markup generators in one place.
"""

from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

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
from app.domains.telegram.rugmunchbot.formatters import dex_buttons, is_owner, web_scan_button


def _main_menu_rows(user_id: int | None = None) -> list[list[InlineKeyboardButton]]:
    """Shared main-menu rows (v7).

    Six capability categories, two buttons per row:
      Scan Token · Market Intel
      AI Chat    · RugMaps
      RugCharts  · Help

    Portfolio & Security were folded into Scan / Market Intel (v7).
    Owner (@cryptorugmunch) additionally sees an Admin button that
    opens the admin sub-menu — hidden from all other users.
    """
    rows = [
        [
            InlineKeyboardButton("\U0001f50d Scan Token", callback_data="menu_cat_scan"),
            InlineKeyboardButton("\U0001f4ca Market Intel", callback_data="menu_cat_market"),
        ],
        [
            InlineKeyboardButton("\U0001f4ac AI Chat", callback_data="menu_cat_ai"),
            InlineKeyboardButton("\U0001f5fa\ufe0f RugMaps", callback_data="menu_cat_rugmaps"),
        ],
        [
            InlineKeyboardButton("\U0001f4c8 RugCharts", callback_data="menu_cat_charts"),
            InlineKeyboardButton("\u2139\ufe0f Help", callback_data="menu_cat_help"),
        ],
        [
            InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL),
            InlineKeyboardButton("\U0001f4e2 Alerts", url=TELEGRAM_CHANNEL),
        ],
    ]
    # Owner-only admin button — hidden from everyone else.
    if user_id is not None and is_owner(user_id):
        rows.append([InlineKeyboardButton("\U0001f6e1\ufe0f Admin", callback_data="menu_cat_admin")])
    return rows


def main_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(_main_menu_rows(user_id))


# ══════════════════════════════════════════════
# v5 — WORLD-CLASS MENU SYSTEM
# Categorized welcome menu + per-category submenus.
# Every submenu has a back button to menu_main.
# ══════════════════════════════════════════════


def welcome_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Main welcome menu — six capability categories (v7).

    Scan Token · Market Intel · AI Chat · RugMaps · RugCharts · Help

    Owner (@cryptorugmunch) additionally sees an Admin button that
    opens the admin sub-menu — hidden from all other users.
    """
    return InlineKeyboardMarkup(_main_menu_rows(user_id))


def _menu_row(label: str, cb: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(label, callback_data=cb)]


def category_scan_kb() -> InlineKeyboardMarkup:
    """🔍 Scan Token submenu (v7).

    Primary actions: Quick Scan (fast) · Deep Scan (full forensic) ·
    Wallet Analysis · Deployer Check. Portfolio & Security commands were
    folded into this category (v7).
    """
    return InlineKeyboardMarkup(
        [
            _menu_row("\u26a1 Quick Scan (fast)", "run_scan"),
            _menu_row("\U0001f52c Deep Scan (full forensic)", "run_deepscan"),
            _menu_row("\U0001f45b Wallet Analysis", "run_wallet"),
            _menu_row("\U0001f575\ufe0f Deployer Check", "run_deployer"),
            [
                InlineKeyboardButton("\U0001f525 Hype", callback_data="run_hype"),
                InlineKeyboardButton("\U0001f6a8 Rug Check", callback_data="run_rugcheck"),
            ],
            [
                InlineKeyboardButton("\U0001f4c1 Portfolio", callback_data="run_portfolio"),
                InlineKeyboardButton("\u2696\ufe0f Compare", callback_data="run_compare"),
            ],
            [
                InlineKeyboardButton("\U0001f440 Watchlist", callback_data="run_watchlist"),
                InlineKeyboardButton("\U0001f514 Alerts", callback_data="run_alerts"),
            ],
            _menu_row("\U0001f50e Investigate (Pro)", "run_investigate"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_charts_kb() -> InlineKeyboardMarkup:
    """📈 RugCharts submenu (v7 — charting-focused; RugMaps split out)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4c8 /chart — Price chart image", "run_chart"),
            _menu_row("\U0001f4ca /ta — Technical analysis & signals", "run_ta"),
            _menu_row("\U0001f4b0 /price — Token price", "run_price"),
            _menu_row("\U0001f4e6 /dex — DEX pairs & liquidity", "run_dex"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_rugmaps_kb() -> InlineKeyboardMarkup:
    """🗺️ RugMaps submenu (v7 — bubble maps & holder clusters)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f5fa\ufe0f /rugmap — Bubble map image", "run_rugmap"),
            _menu_row("\U0001f465 /holders — Top holders & clusters (Scout+)", "run_holders"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_market_kb() -> InlineKeyboardMarkup:
    """📊 Market Intel submenu (v7 — trending, price, sentiment, news, social)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f525 Trending Tokens", "run_trending"),
            _menu_row("\U0001f4b0 Price Check", "run_price"),
            _menu_row("\U0001f4ca Market Sentiment", "run_sentiment"),
            _menu_row("\U0001f4f0 News", "run_news"),
            _menu_row("\U0001f4e3 Social Signals", "run_social"),
            [
                InlineKeyboardButton("\U0001f4e6 DEX & Liquidity", callback_data="run_dex"),
                InlineKeyboardButton("\U0001f465 Holders", callback_data="run_holders"),
            ],
            _menu_row("\U0001f9e0 Smart Money", "run_smartmoney"),
            _menu_row("\U0001f50d Fund Trace (Hunter+)", "run_trace"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_ai_kb() -> InlineKeyboardMarkup:
    """💬 AI Chat submenu (v6 — includes /chat reset hint)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4ac /chat — Freestyle AI chat", "run_chat"),
            _menu_row("\U0001f50d /ask — Deep RAG answer", "run_ask"),
            _menu_row("\U0001f9e0 /research — RAG deep research (Hunter+)", "run_research"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_portfolio_kb() -> InlineKeyboardMarkup:
    """📋 Portfolio submenu."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4c1 /portfolio — Wallet holdings", "run_portfolio"),
            _menu_row("\U0001f440 /watchlist — Tracked tokens", "run_watchlist"),
            _menu_row("\U0001f514 /alerts — Price alerts", "run_alerts"),
            _menu_row("\U0001f4b5 /simulate — Simulate a trade", "run_simulate"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_security_kb() -> InlineKeyboardMarkup:
    """🛡️ Security & Education submenu (v6 — new category)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f6a8 /rugcheck — Rug pull checklist", "run_rugcheck"),
            _menu_row("\U0001f4da /scamschool — Learn scam patterns", "run_scamschool"),
            _menu_row("\U0001f6ab /report — Report a scam address", "run_report"),
            _menu_row("\U0001f575\ufe0f /investigate — Deep report (Pro)", "run_investigate"),
            _menu_row("\U0001f512 /scan — Security audit", "run_scan"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_account_kb() -> InlineKeyboardMarkup:
    """👤 Account submenu (kept for backward compatibility)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4cb /account — Your dashboard", "run_account"),
            _menu_row("\u2b50 /pricing — Upgrade tier", "run_pricing"),
            _menu_row("\U0001f91d /refer — Earn bonus scans", "run_refer"),
            _menu_row("\U0001f4da /scamschool — Learn scam patterns", "run_scamschool"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_help_kb() -> InlineKeyboardMarkup:
    """Help & About submenu."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4d6 /help — Full command reference", "run_help"),
            _menu_row("\U0001f4f1 Telegram Channel", "open_channel"),
            _menu_row("\U0001f4ac Community Chat", "open_group"),
            _menu_row("\U0001f310 Website", "open_website"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def admin_kb() -> InlineKeyboardMarkup:
    """Owner admin dashboard keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4ca Stats", callback_data="admin_stats"),
                InlineKeyboardButton("\U0001f465 User Lookup", callback_data="admin_user_prompt"),
            ],
            [
                InlineKeyboardButton("\U0001f4e2 Broadcast", callback_data="admin_broadcast_prompt"),
                InlineKeyboardButton("\U0001f4dd Logs", callback_data="admin_logs"),
            ],
            [
                InlineKeyboardButton("\U0001f6e1\ufe0f Bot ON/OFF", callback_data="admin_toggle_bot"),
                InlineKeyboardButton("\U0001f4ac Chat ON/OFF", callback_data="admin_toggle_chat"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def owner_admin_reply_kb() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard pinned to the input field for the owner (v7).

    Shows the admin commands as tappable buttons in every private chat with
    the bot. The button labels are plain text; the message handler routes
    them to the matching /admin_* command (see handlers/message.py).
    """
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("\U0001f6e1\ufe0f Admin Dashboard"),
                KeyboardButton("\U0001f4ca Stats"),
            ],
            [
                KeyboardButton("\U0001f4e2 Broadcast"),
                KeyboardButton("\U0001f465 User Lookup"),
            ],
            [
                KeyboardButton("\U0001f504 Bot ON/OFF"),
                KeyboardButton("\U0001f4ac Chat ON/OFF"),
            ],
            [KeyboardButton("\U0001f3d9\ufe0f Main Menu")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="RugMunch Admin console",
    )


def admin_confirm_kb(action: str, payload: str = "") -> InlineKeyboardMarkup:
    """Generic confirm/cancel keyboard for destructive admin actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"admin_confirm_{action}"),
                InlineKeyboardButton("\u274c Cancel", callback_data="admin_cancel"),
            ]
        ]
    )


def scan_actions_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Post-scan action row (v6): 📊 View Chart · 🗺️ View RugMap · 📤 Share · 🔍 Deep Scan."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4c8 View Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton("\U0001f5fa\ufe0f View RugMap", callback_data=f"rugmap_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
                InlineKeyboardButton("\U0001f52c Deep Scan", callback_data=f"deep_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
            InlineKeyboardButton("\U0001f50d Research", callback_data=f"research_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"),
            InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f514 Price Alert", callback_data=f"alert_{address}_{chain}"),
            InlineKeyboardButton("\U0001f4c8 Sentiment", callback_data=f"sentiment_{address}_{chain}"),
        ],
    ]
    dex_btns = dex_buttons(address, chain)
    for i in range(0, len(dex_btns), 2):
        rows.append(dex_btns[i : i + 2])
    # v6: quick chart / rugmap / share actions
    rows.append(
        [
            InlineKeyboardButton("\U0001f4c8 Chart", callback_data=f"chart_{address}_{chain}"),
            InlineKeyboardButton("\U0001f5fa\ufe0f RugMap", callback_data=f"rugmap_{address}_{chain}"),
            InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
        ]
    )
    # Upgrade nudge for free users
    rows.append([InlineKeyboardButton("\u2b50 Unlock Premium Features", callback_data="menu_pricing")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def pricing_kb() -> InlineKeyboardMarkup:
    rows = []
    for key in ["scout", "hunter", "pro"]:
        t = TIERS[key]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{t['emoji']} {t['name']} - ${t['price_monthly']}/mo",
                    callback_data=f"sub_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f310 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")])
    rows.append([InlineKeyboardButton("\U0001f4e7 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton("\U0001f52c Scan Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "scans":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\U0001f916 AI Message Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "ai_msgs":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def buy_stars_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in BUY_STARS_PACKS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                    callback_data=f"buystars_{key}",
                )
            ]
        )
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


def holders_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"),
                InlineKeyboardButton("\U0001f52c Deep Scan", callback_data=f"deep_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def trace_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("\U0001f9db Wallet Analysis", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("\U0001f50d Scan Token", callback_data=f"scan_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f52c Deep Scan", callback_data=f"deep_{address}_{chain}"),
                InlineKeyboardButton("\U0001f440 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def investigate_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f310 Full Report", url=f"{WEBSITE_URL}/markets/token/{address}")],
            [
                InlineKeyboardButton("\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f50d Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def _share_token_url(address: str, chain: str) -> str:
    """t.me/share/url deep link advertising a scanned token (v6)."""
    from urllib.parse import quote

    return (
        "https://t.me/share/url?url="
        + quote(f"{WEBSITE_URL}/scanner?token={address}&chain={chain}")
        + "&text="
        + quote("Scanned with RugMunch — check this token before you buy!")
    )


def deep_scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Extended keyboard shown after scan results with all premium actions."""
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f50d Research", callback_data=f"research_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4c8 Sentiment", callback_data=f"sentiment_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"),
                InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f4c8 Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton("\U0001f5fa\ufe0f RugMap", callback_data=f"rugmap_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
            ],
            [
                InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
                InlineKeyboardButton("\U0001f575\ufe0f Investigate", callback_data=f"investigate_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def upgrade_nudge_kb(current_tier: str) -> InlineKeyboardMarkup:
    """Keyboard shown when user hits usage limits."""
    buttons = []
    if current_tier == "free":
        buttons.append([InlineKeyboardButton("🔍 Scout ($29.99/mo)", callback_data="sub_scout")])
        buttons.append([InlineKeyboardButton("🎯 Hunter ($49.99/mo)", callback_data="sub_hunter")])
    elif current_tier == "scout":
        buttons.append([InlineKeyboardButton("🎯 Hunter ($49.99/mo)", callback_data="sub_hunter")])
        buttons.append([InlineKeyboardButton("👑 Pro ($99.99/mo)", callback_data="sub_pro")])
    elif current_tier == "hunter":
        buttons.append([InlineKeyboardButton("👑 Pro ($99.99/mo)", callback_data="sub_pro")])
    buttons.append([InlineKeyboardButton("💳 View All Plans", callback_data="menu_pricing")])
    buttons.append([InlineKeyboardButton("🔄 Buy Top-Up", callback_data="menu_topup")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


def api_kb(has_token: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for /api command."""
    buttons = [
        [InlineKeyboardButton("📖 API Documentation", url=f"{WEBSITE_URL}/docs")],
    ]
    if has_token:
        buttons.append([InlineKeyboardButton("🔄 Revoke Token", callback_data="api_revoke")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)
