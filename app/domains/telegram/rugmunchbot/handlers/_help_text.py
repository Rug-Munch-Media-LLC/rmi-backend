#!/usr/bin/env python3
"""
RugMunchBot -- /help & /menu reference text & rendering helpers.

Extracted from ``handlers.scanning.py`` (degodding 2026-08-14) — pure text
constants and pure render functions only. No network, DB, or Telegram API
access: rendering the help reference is deterministic given ``config``.

Everything here is re-exported by ``handlers.scanning`` so existing
importers of ``scanning.HELP_TEXT`` / ``_HELP_SECTIONS`` / ``CHANNEL_LABELS``
keep working unchanged (same objects — ``HELP_TEXT`` is built once at import
time, exactly as before).

v13 (2026-08-16): rebuilt the two user-facing guides so they ACTUALLY guide:

  * ``/help``  (``HELP_TEXT``) — guided on-ramp: what the bot is ("Rug Munch
    Intelligence Analyst"), a 3-step getting-started flow, flagship commands
    with concrete examples, a "what you can ask" section, then the compact
    grouped reference. Deliberately kept under Telegram's 4096-char message
    limit (the old reference was ~5.3k chars and would fail to send).

  * ``/menu``  (``MENU_TEXT``) — capability atlas grouped by intent
    (Scan & Safety / Prices & Charts / Portfolio & Wallets / News & Intel /
    Education / Account & Community). Every command listed is a REAL
    registered command (verified against ``bot.py``); each line carries a
    one-line description + example usage.

All command names below were cross-checked against the actual registrations
in ``bot.py`` (``_reg(app, ...)`` + ``register_market/prediction/defi/
trace_commands``). Owner-only ``/admin_*`` commands are documented in the
admin panel, not here. There is intentionally NO ``/me`` command anywhere
(not registered) — it is not listed.
"""

from __future__ import annotations

import html

from app.domains.telegram.rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    sep,
    thin_sep,
)

# ══════════════════════════════════════════════
# /help — grouped core reference (v13)
# Compact "core cheat sheet" used inside the guided /help. The FULL command
# atlas (every registered command, grouped by intent with examples) lives in
# ``_MENU_SECTIONS`` → ``/menu``. All eight group headers are kept so the
# BotFather ordering, the welcome-menu sections and the tests stay aligned.
# Rendered with HTML parse mode — every <code> tag is balanced.
# ══════════════════════════════════════════════
_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Scan & Security",
        [
            ("scan", "address - quick security audit"),
            ("deepscan", "address - ML deep scan (fraud-GNN, ABI, sell sim)"),
            ("scandeep", "address - full multi-phase forensic scan"),
            ("sentinelscan", "address - AEGIS 9-module scan"),
            ("rugcheck", "address - red flag checklist"),
            ("compare", "a b - side-by-side compare"),
            ("deployer", "address - deployer fingerprint"),
            ("hype", "address - 0-100 hype score"),
            ("report", "address - report a scam"),
        ],
    ),
    (
        "Wallet & Tracing",
        [
            ("wallet", "address - wallet forensics"),
            ("portfolio", "address - wallet holdings"),
            ("trace", "address - cross-chain fund trace"),
            ("smartmoney", "address - smart money detection"),
        ],
    ),
    (
        "Market & Analysis",
        [
            ("market", "- market overview (BTC/ETH, F&G)"),
            ("price", "address - token price"),
            ("quick", "symbol - instant price check"),
            ("chart", "address - chart image (OHLCV)"),
            ("ta", "address - technical analysis"),
            ("trending", "[chain] - hot tokens right now"),
            ("movers", "- biggest 24h gainers & losers"),
        ],
    ),
    (
        "News & Alerts",
        [
            ("news", "- latest crypto news"),
            ("alerts", "- manage price & activity alerts"),
            ("watch", "address - add to watchlist"),
            ("watchlist", "- view your watchlist"),
            ("whale", "address - whale alerts"),
        ],
    ),
    (
        "Chat & Learn",
        [
            ("chat", "question - freestyle AI chat"),
            ("ask", "question - deep RAG answer"),
            ("research", "query - deep research (Hunter+)"),
            ("faq", "- how the bot works, free? accuracy"),
        ],
    ),
    (
        "OSINT & Forensics",
        [
            ("osint", "username - find public accounts (maigret)"),
            ("email", "email - breach & reputation check"),
            ("track", "address - wallet entity/sanction/scam check"),
        ],
    ),
    (
        "Premium",
        [
            ("mev", "address - MEV risk & safe swaps"),
            ("honeypot", "address - honeypot detector"),
            ("lphealth", "address - liquidity pool health"),
            ("deathclock", "address - survival prediction"),
        ],
    ),
    (
        "Account & Extras",
        [
            ("account", "- dashboard & usage stats"),
            ("pricing", "- subscription plans"),
            ("topup", "- buy extra scans"),
            ("scamschool", "- learn the scam playbook"),
        ],
    ),
]

_HELP_EMOJI: dict[str, str] = {
    "Scan & Security": "\U0001f6e1\ufe0f",
    "Wallet & Tracing": "\U0001f50d",
    "Market & Analysis": "\U0001f4c8",
    "News & Alerts": "\U0001f4f0",
    "Chat & Learn": "\U0001f4ac",
    "OSINT & Forensics": "\U0001f575\ufe0f",
    "Premium": "\U0001f451",
    "Account & Extras": "\U0001f4b3",
}

# ══════════════════════════════════════════════
# /menu — capability atlas grouped by intent (v13)
# One entry per REAL registered command (verified against bot.py). The
# ``desc`` carries a one-line description + a concrete example usage.
# ══════════════════════════════════════════════
_MENU_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Scan & Safety",
        [
            ("scan", "<code>address</code> - quick security audit"),
            ("deepscan", "<code>address</code> - ML deep scan (fraud-GNN, ABI)"),
            ("scandeep", "<code>address</code> - full forensic scan"),
            ("sentinelscan", "<code>address</code> - AEGIS 9-module scan"),
            ("rugcheck", "<code>address</code> - red-flag checklist"),
            ("deployer", "<code>address</code> - deployer fingerprint"),
            ("hype", "<code>address</code> - 0-100 hype score"),
            ("compare", "<code>a</code> <code>b</code> - side-by-side compare"),
            ("investigate", "<code>target</code> - deep report (Pro)"),
            ("rugmap", "<code>address</code> - bubble-map image"),
            ("report", "<code>address</code> - report a scam"),
            ("honeypot", "<code>address</code> - honeypot detector"),
            ("impersonation", "<code>address</code> - fake token check"),
            ("mev", "<code>address</code> - MEV risk & safe swaps"),
            ("lpcheck", "<code>address</code> - LP health check"),
            ("lphealth", "<code>address</code> - liquidity pool health"),
            ("deathclock", "<code>address</code> - survival prediction"),
            ("laundry", "<code>address</code> - laundering detector"),
            ("cluster", "<code>address</code> - wallet clustering"),
        ],
    ),
    (
        "Prices & Charts",
        [
            ("<code>$SYMBOL</code>", "- instant price (e.g. $SOL or $BONK)"),
            ("price", "<code>address</code> - token price"),
            ("quick", "<code>symbol</code> - instant price check"),
            ("chart", "<code>address</code> - chart image (OHLCV)"),
            ("ta", "<code>address</code> - technical analysis & signals"),
            ("dex", "<code>address</code> - DEX pairs & liquidity"),
            ("market", "- market overview (BTC/ETH, F&G)"),
            ("trending", "[chain] - hot tokens right now"),
            ("movers", "- biggest 24h gainers & losers"),
            ("launches", "[chain] - new launch feed"),
            ("whales", "- whale movement alerts"),
            ("sentiment", "- fear/greed & indicators"),
            ("holders", "<code>address</code> - top holders (Scout+)"),
            ("yield", "- DeFi yield opportunities"),
            ("defi", "<code>protocol</code> - DeFi analytics"),
            ("polymarket", "- prediction markets"),
            ("election", "- election odds"),
            ("odds", "- sports & event odds"),
        ],
    ),
    (
        "Portfolio & Wallets",
        [
            ("wallet", "<code>address</code> - wallet forensics"),
            ("portfolio", "<code>address</code> - wallet holdings"),
            ("simulate", "<code>address</code> - buy/sell simulation"),
            ("smartmoney", "<code>address</code> - smart money detection"),
            ("trace", "<code>address</code> - cross-chain fund trace"),
            ("flow", "<code>address</code> - money flow tracking"),
            ("dumps", "<code>address</code> - dump pattern detector"),
            ("track", "<code>address</code> - wallet entity/sanction check"),
            ("watchlist", "- view your watchlist"),
            ("watch", "<code>address</code> - add to watchlist"),
            ("unwatch", "<code>address</code> - remove from watchlist"),
        ],
    ),
    (
        "News & Intel",
        [
            ("news", "- latest crypto news"),
            ("events", "- news events & price impact"),
            ("chat", "<code>question</code> - freestyle AI chat"),
            ("ask", "<code>question</code> - deep RAG answer"),
            ("research", "<code>query</code> - deep research (Hunter+)"),
            ("alerts", "- manage price & activity alerts"),
            ("alert", "ABOVE|BELOW <code>addr</code> <code>price</code>"),
            ("whale", "<code>address</code> - whale movement alerts"),
            ("webhook", "- custom webhook alerts (Pro)"),
            ("api", "- API access token (Pro)"),
            ("osint", "<code>username</code> - find linked public accounts"),
            ("osintall", "<code>target</code> - run every OSINT module"),
            ("email", "<code>email</code> - breach & reputation check"),
            ("phone", "<code>phone</code> - carrier & region lookup"),
            ("face", "<code>photo</code> - face detection & reverse search"),
        ],
    ),
    (
        "Education",
        [
            ("scamschool", "- learn the scam playbook"),
            ("faq", "- how the bot works, is it free, accuracy"),
            ("start", "- welcome & quick start"),
            ("help", "- full command guide"),
            ("menu", "- this menu"),
        ],
    ),
    (
        "Account & Community",
        [
            ("account", "- dashboard & usage stats"),
            ("pricing", "- subscription plans"),
            ("topup", "- buy extra scans"),
            ("buystars", "- buy Stars"),
            ("refer", "- invite friends, earn scans"),
            ("mygroup", "- your creator coin group"),
            ("groupinfo", "- group stats (admin)"),
            ("voicechat", "- manage voice chat (admin)"),
            ("banuser", "<code>user_id</code> - group ban (admins)"),
        ],
    ),
]

_MENU_EMOJI: dict[str, str] = {
    "Scan & Safety": "\U0001f6e1\ufe0f",
    "Prices & Charts": "\U0001f4c8",
    "Portfolio & Wallets": "\U0001f45b",
    "News & Intel": "\U0001f4f0",
    "Education": "\U0001f4da",
    "Account & Community": "\U0001f464",
}

# ── Community channels surfaced in /help (config.CHANNELS-backed) ──────────

#: Display labels for the community channels, keyed by config.CHANNELS keys.
CHANNEL_LABELS: dict[str, str] = {
    "main": "Main",
    "news": "News",
    "alerts": "Alerts",
    "alpha": "Alpha",
    "scans": "Scans",
    "whale": "Whale",
}


def _channel_url(key: str, chat_id: object) -> str | None:
    """Resolve one ``config.CHANNELS`` entry to a Telegram link.

    ``alerts`` uses the verified public handle (``TELEGRAM_CHANNEL``);
    ``alpha`` is the ``@rmialpha`` public handle. Remaining channels fall
    back to the ``https://t.me/c/<id>`` deep link derived from their
    numeric chat id (the ``-100`` supergroup prefix is stripped). Returns
    ``None`` when no link can be resolved — unresolvable channels are
    simply omitted from the rendered section.
    """
    if key == "alerts" and TELEGRAM_CHANNEL:
        return TELEGRAM_CHANNEL
    if key == "alpha":
        return "https://t.me/rmialpha"
    numeric = str(chat_id or "").strip()
    if numeric.startswith("-100") and numeric[4:].isdigit():
        return f"https://t.me/c/{numeric[4:]}"
    return None


def _channels_section() -> str:
    """Render the community-channels block for /help (HTML-safe)."""
    entries: list[str] = []
    for key, chat_id in CHANNELS.items():
        url = _channel_url(key, chat_id)
        if url is None:
            continue
        label = CHANNEL_LABELS.get(key, key.capitalize())
        entries.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
    if not entries:
        return ""
    return f"\U0001f4e2 <b>Channels</b>\n{thin_sep()}\n" + "\n".join(entries)


def _build_help_text() -> str:
    """Render the guided /help reference (HTML) — the on-ramp to the bot.

    v13: what the bot is, a 3-step getting-started flow, flagship commands
    with concrete examples, a "what you can ask" section, then the compact
    grouped reference. Stays under Telegram's 4096-char message limit.
    """
    s = sep()
    d = thin_sep()
    parts = [
        f"\U0001f4d6 <b>Rug Munch Intelligence - Command Guide</b>\n{s}",
        "\U0001f575\ufe0f <b>Rug Munch Bot</b> is the AI "
        "<b>Rug Munch Intelligence Analyst</b> \u2014 paste any token "
        "address or wallet and I'll audit for scams, run forensics and "
        "give a risk score before you buy. Built by <b>Rug Munch "
        "Intelligence</b> \u2014 <b>Rug Munch Media LLC</b> (Wyoming).\n",
        f"\U0001f680 <b>GETTING STARTED</b> (3 steps)\n{d}",
        "1\ufe0f\u20e3 /scan <code>address</code> \u2014 audit before you buy\n"
        "2\ufe0f\u20e3 <code>$SOL</code> or /trending \u2014 see what's moving\n"
        "3\ufe0f\u20e3 /chat <code>question</code> \u2014 ask me anything, e.g. "
        "<i>\u201cis this safe?\u201d</i>\n",
        f"\U0001f9f0 <b>HOW TO USE</b> (examples)\n{d}",
        "/scan <code>address</code> \u2014 quick security audit\n"
        "/deepscan <code>address</code> \u2014 ML deep scan (fraud-GNN)\n"
        "/rugcheck <code>address</code> \u2014 red-flag checklist\n"
        "/wallet <code>address</code> \u2014 wallet forensics\n"
        "/portfolio <code>address</code> \u2014 wallet holdings\n"
        "<code>$SOL</code> \u2014 instant price \u00b7 /chart <code>address</code> \u2014 chart + TA\n"
        "/news \u2014 headlines \u00b7 /trending \u2014 hot tokens now\n"
        "/ask <code>question</code> \u2014 deep RAG answers \u00b7 /chat \u2014 freestyle AI\n",
    ]
    for header, cmds in _HELP_SECTIONS:
        emoji = _HELP_EMOJI.get(header, "\U0001f4d6")
        parts.append(f"\n{emoji} <b>{header}</b>\n{d}")
        for cmd, desc in cmds:
            parts.append(f"/{cmd} {desc}")
    parts.append(
        f"\n\U0001f4ac <b>WHAT YOU CAN ASK</b> (via /chat or /ask)\n{d}\n"
        "\U0001f4ca Market analysis \u2014 why is BTC dumping?\n"
        "\U0001f4c8 Technical analysis \u2014 read the chart for $BONK\n"
        "\U0001f6e1\ufe0f Scam detection \u2014 is this address a honeypot?\n"
        "\U0001f4da Token education \u2014 explain pump &amp; dump\n"
        "\U0001f9d9 Crypto lore \u2014 tell me about the DAO hack\n"
    )
    channels = _channels_section()
    if channels:
        parts.append(f"\n{channels}")
    parts.append(
        f"\n\U0001f4a1 <b>PRO TIPS</b>\n{d}\n"
        "\u2022 Paste any <code>0x...</code> address to auto-scan\n"
        "\u2022 Type <code>$PEPE</code> for instant price check\n"
        "\u2022 Tap the buttons under any result (Chart / RugMap / Deep Scan)\n"
        "\u2022 See a scam? /report <code>address</code> keeps us all safer\n\n"
        "\U0001f4d6 Full command list: /menu \u2014 grouped by category"
    )
    parts.append(footer_links())
    return "\n".join(parts)


def _build_menu_text() -> str:
    """Render the /menu capability atlas (HTML), grouped by intent.

    v13: five (plus Account & Community) intent groups, every line a REAL
    registered command with a one-line description + example usage. The
    quick-action inline keyboard is built in ``keyboards.menu_kb()``.
    """
    s = sep()
    d = thin_sep()
    parts = [
        f"\U0001f5fa\ufe0f <b>Rug Munch Bot \u2014 Command Menu</b>\n{s}",
        "Tap a button or type any command.\n",
    ]
    for header, cmds in _MENU_SECTIONS:
        emoji = _MENU_EMOJI.get(header, "\U0001f4d6")
        parts.append(f"\n{emoji} <b>{header}</b>\n{d}")
        for cmd, desc in cmds:
            if cmd.startswith("<code>"):
                parts.append(f"\u2022 {cmd} {desc}")
            else:
                parts.append(f"\u2022 /{cmd} {desc}")
    return "\n".join(parts)


HELP_TEXT: str = _build_help_text()
MENU_TEXT: str = _build_menu_text()
