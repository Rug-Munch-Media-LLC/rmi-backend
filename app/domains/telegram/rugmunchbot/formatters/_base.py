"""Core formatting & utility helpers for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
address detection, text/link helpers, welcome/paywall text and the
explainable-scoring + funding-analysis blocks. ``formatters.py`` re-exports
every public name; imports elsewhere are unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from telegram import InlineKeyboardButton

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    CHAIN_IDS,
    DEX_REF_LINKS,
    TELEGRAM_CHANNEL,
    TIERS,
    WEB_APP_URL,
)

# ── FREE PUBLIC BETA (2026-08) ──────────────────────────────────────────
# rugmunchbot is in FREE PUBLIC BETA: every premium feature is unlocked for
# everyone while we test. Billing surfaces stay functional (they are the
# future revenue path) but beta status must be prominent. Remove this block
# and the free-tier quota bump in config.py when beta ends.
BETA_BANNER = (
    "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all premium features "
    "unlocked while we test.\n"
    "\U0001f4c5 <b>Premium plans coming soon</b> \u2014 beta users get "
    "launch discounts."
)
BETA_STATUS_LINE = "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all features unlocked \U0001f389"
BETA_PAYMENTS_LINE = (
    "\U0001f680 <b>In beta</b> \u2014 everything\u2019s free right now, but payments live for early supporters."
)

# --- Address detection helpers ---
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_evm(addr: str) -> bool:
    from app.core.shared_utils import is_evm_address

    return is_evm_address(addr)


def is_sol(addr: str) -> bool:
    from app.core.shared_utils import is_solana_address

    return is_solana_address(addr)


def detect_chain(addr: str) -> str:
    if is_evm(addr):
        return "ethereum"
    if is_sol(addr):
        return "solana"
    return "unknown"


def short_addr(addr: str) -> str:
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


# --- Text helpers ---
def sep(char: str = "\u2501", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "\u2500", length: int = 24) -> str:
    return char * length


def footer_links(address: str = "", chain: str = "") -> str:
    """Bot footer with links. When a token is provided, includes
    RugCharts + RugMaps deep links so users can jump to visual analysis."""
    base = f'\n{sep()}\n\U0001f50d RugMunch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'
    if address:
        base += (
            f'\n\U0001f4c8 <a href="https://rugmunch.io/rugcharts?token={address}&chain={chain}">RugCharts</a>'
            f' | \U0001f5fa\ufe0f <a href="https://rugmunch.io/rugmaps?token={address}&chain={chain}">RugMaps</a>'
        )
    return base


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("\U0001f310 Full Report on RugMunch.io", url=url)


# DexScreener chain slugs (match ``chainId`` values from DexScreener pairs).
DEXSCREENER_CHAINS = {
    "solana": "solana",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon",
    "avalanche": "avax",
    "optimism": "optimism",
}


def dexscreener_url(address: str, chain: str) -> str:
    """DexScreener token page URL — tap-to-open chart for any token."""
    slug = DEXSCREENER_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    return f"https://dexscreener.com/{slug}/{address}"


def explorer_url(address: str, chain: str) -> str:
    """Block explorer URL for a token/wallet on the given chain."""
    chain_l = (chain or "").lower()
    if chain_l == "solana":
        return f"https://solscan.io/account/{address}"
    domains = {
        "ethereum": "etherscan.io",
        "bsc": "bscscan.com",
        "polygon": "polygonscan.com",
        "arbitrum": "arbiscan.io",
        "optimism": "optimistic.etherscan.io",
        "base": "basescan.org",
        "avalanche": "snowtrace.io",
        "fantom": "ftmscan.com",
        "tron": "tronscan.org",
    }
    domain = domains.get(chain_l, "etherscan.io")
    return f"https://{domain}/address/{address}"


def dex_buttons(token: str, chain: str) -> list[InlineKeyboardButton]:
    buttons = []
    for _key, dex in DEX_REF_LINKS.items():
        if chain.lower() in dex.get("chains", []):
            url = dex["url"].format(  # type: ignore[attr-defined]  # PTB None-able fields
                token=token,
                chain=chain,
                chain_id=CHAIN_IDS.get(chain.lower(), 1),
            )
            buttons.append(InlineKeyboardButton(f"{dex['emoji']} {dex['name']}", url=url))
    return buttons


def social_proof_text() -> str:
    try:
        conn = db.get_db()
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
        conn.close()
        users_str = f"{total_users:,}" if total_users else "1,000+"
        scans_str = f"{total_scans:,}" if total_scans else "50,000+"
        return f"\U0001f465 {users_str} users | \U0001f50d {scans_str} scans completed"
    except (ConnectionError, OSError):
        return "\U0001f465 1,000+ users | \U0001f50d 50,000+ scans completed"


def welcome_card_text() -> str:
    """v9 welcome card — capability overview shown by /start and the main menu.

    Covers the five main-menu sections (Scan · Intelligence · Alerts ·
    Wallet · Help) plus the quick-start tips. Account & AI Chat are
    reachable from the same menu.
    """
    proof = social_proof_text()
    return (
        f"\U0001f6e1\ufe0f <b>RugMunch Intelligence</b>\n"
        f"<i>The Bloomberg Terminal of Shitcoins</i>\n\n"
        f"{proof}\n\n"
        f"<b>I can scan tokens, analyze wallets, show charts, run "
        f"RugMaps bubble maps, answer crypto questions via AI chat, track "
        f"your portfolio, and more.</b>\n"
        f"\U0001f4cc Try <code>/scan</code> or <code>/chat</code> to get started!\n\n"
        f"<b>Sections:</b>\n"
        f"\U0001f50d <b>Scan</b> — audits, deep/SENTINEL, deployer, rug checks\n"
        f"\U0001f9e0 <b>Intelligence</b> — market, trending, news, AI chat\n"
        f"\U0001f514 <b>Alerts</b> — price alerts, watchlist, whale alerts\n"
        f"\U0001f45b <b>Wallet</b> — forensics, portfolio, watchlist\n"
        f"\u2139\ufe0f <b>Help</b> — full command reference\n\n"
        f"\U0001f4f3 <b>Free:</b> {TIERS['free']['scans_per_week']} scans + "
        f"{TIERS['free']['ai_msgs_per_week']} AI msgs/week\n"
        f"{BETA_BANNER}\n\n"
        f"<b>Quick Start:</b>\n"
        f"\U0001f4cc Paste any contract address \u2192 auto-scan\n"
        f"\U0001f4cc Type <code>$TOKEN</code> \u2192 instant price\n"
        f"\U0001f4cc /chat <code>your question</code> \u2192 talk to me\n"
        f"\U0001f4cc Pick a section below \U0001f447"
    )


def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)
    # Public beta: hitting the (boosted) weekly quota is a REAL limit, but
    # tell free users it's a beta allocation that resets — not a paywall.
    beta_note = ""
    if tier == "free":
        beta_note = (
            "\n\U0001f680 <b>Beta:</b> your weekly limit is boosted for the "
            "public beta \u2014 resets Monday 00:00 UTC.\n"
        )
    return (
        f"\u26a1 <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"\U0001f4ca <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"\U0001f381 <b>Bonus Balance:</b> {bonus} extra\n"
        f"{beta_note}"
        f"\n<b>Unlock more power:</b>\n\n"
        f"\U0001f50d <b>Scout ($29.99/mo)</b> - 150 scans/wk + bundle detection\n"
        f"\U0001f3af <b>Hunter ($49.99/mo)</b> - 400 scans/wk + wallet forensics\n"
        f"\U0001f451 <b>Pro ($99.99/mo)</b> - 1,000 scans/wk + smart money alerts\n\n"
        f"Or buy one-time top-ups - <b>never expire!</b>\n"
        f"\U0001f50b 10 scans = \u2b5075 | 50 scans = \u2b50300 | 100 scans = \u2b50500"
    )


# --- Risk helpers ---
def _risk_indicator(score: float) -> str:
    if score >= 70:
        return "\U0001f534"
    if score >= 40:
        return "\U0001f7e1"
    return "\U0001f7e2"


def _price_diff_pct(p1: float, p2: float) -> str:
    if p1 <= 0 or p2 <= 0:
        return "N/A"
    diff = ((p1 - p2) / max(p1, p2)) * 100
    abs_diff = abs(diff)
    if abs_diff < 0.01:
        return "\u2248 same"
    cheaper = "T1" if diff < 0 else "T2"
    return f"{cheaper} {abs_diff:.2f}% cheaper"


# --- Explainable scoring breakdown ---
DIMENSION_LABELS = {
    "security": "\U0001f512 Contract Security",
    "liquidity": "\U0001f4a7 Liquidity",
    "holders": "\U0001f465 Holders",
    "deployer": "\U0001f468\u200d\U0001f4bb Deployer",
    "market": "\U0001f4ca Market",
}


def format_scoring_breakdown(breakdown: dict[str, Any]) -> str:
    """Render the v2 explainable per-dimension scoring breakdown.

    Each dimension carries score/max/delta/flags. Output is a compact
    table with a filled bar so users can see WHY a token scored the way
    it did at a glance.
    """
    if not breakdown:
        return ""
    lines = ["", "\U0001f9e9 <b>Scoring Breakdown</b>", thin_sep()]
    for dim, weight in DIMENSION_LABELS.items():
        entry = breakdown.get(dim)
        if not isinstance(entry, dict):
            continue
        score = float(entry.get("score", 0) or 0)
        maximum = float(entry.get("max", 0) or 0)
        delta = float(entry.get("delta", 0) or 0)
        bar_len = 10
        filled = round((score / maximum) * bar_len) if maximum else 0
        filled = max(0, min(filled, bar_len))
        bar = "\u2b1b" * filled + "\u2b1c" * (bar_len - filled)
        marker = "\U0001f7e2" if delta >= 0 else "\U0001f534"
        lines.append(f"  {marker} {weight}: {bar} {score:.0f}/{maximum:.0f}")
        flags = entry.get("flags") or []
        if flags:
            for fl in flags[:2]:
                lines.append(f"      \u26a0\ufe0f {fl}")
    return "\n".join(lines)


def format_funding_analysis(r: dict[str, Any]) -> str:
    """Render the funding analysis block (exchange-funded / fresh / scam overlap).

    Reads the convenience keys populated by the scanner layer:
    ``exchange_funded_pct``, ``fresh_wallet_pct``, ``holder_overlap_pct``.
    Missing values render as N/A so the section is stable across v1/v2.
    """
    exchange = r.get("exchange_funded_pct")
    fresh = r.get("fresh_wallet_pct")
    overlap = r.get("holder_overlap_pct")
    if exchange is None and fresh is None and overlap is None:
        return ""
    lines = ["", "\U0001f4b8 <b>Funding Analysis</b>", thin_sep()]
    if exchange is not None:
        emoji = "\U0001f534" if exchange > 50 else "\U0001f7e1" if exchange > 30 else "\U0001f7e2"
        lines.append(f"{emoji} Exchange-Funded: <b>{exchange:.0f}%</b>")
    else:
        lines.append("\U0001f7e0 Exchange-Funded: <b>N/A</b>")
    if fresh is not None:
        emoji = "\U0001f534" if fresh > 40 else "\U0001f7e1" if fresh > 20 else "\U0001f7e2"
        lines.append(f"{emoji} Fresh Wallets: <b>{fresh:.0f}%</b>")
    else:
        lines.append("\U0001f7e0 Fresh Wallets: <b>N/A</b>")
    if overlap is not None:
        emoji = "\U0001f534" if overlap > 30 else "\U0001f7e1" if overlap > 10 else "\U0001f7e2"
        lines.append(f"{emoji} Holder Overlap w/ Scams: <b>{overlap:.0f}%</b>")
    else:
        lines.append("\U0001f7e0 Holder Overlap w/ Scams: <b>N/A</b>")
    return "\n".join(lines)
