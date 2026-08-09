#!/usr/bin/env python3
"""
RugMunchBot -- Message formatting & utility helpers.

All formatting, text generation, and small utility functions
used by handlers and other modules.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from telegram import InlineKeyboardButton

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    CHAIN_IDS,
    DEX_REF_LINKS,
    OWNER_IDS,
    TELEGRAM_CHANNEL,
    TIERS,
    WEB_APP_URL,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
    threat_indicator,
)

logger = get_logger("rmi_bot")

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


def is_owner(uid: int) -> bool:
    return uid in OWNER_IDS


# --- Text helpers ---
def sep(char: str = "\u2501", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "\u2500", length: int = 24) -> str:
    return char * length


def footer_links() -> str:
    return f'\n{sep()}\n\U0001f50d RugMunch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'


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
            url = dex["url"].format(
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
    """v7 welcome card — capability overview shown by /start and the main menu.

    Covers the six main-menu categories (Scan Token · Market Intel · AI Chat ·
    RugMaps · RugCharts · Help) plus the quick-start tips. Portfolio &
    Security were folded into Scan / Market Intel.
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
        f"<b>Capabilities:</b>\n"
        f"\U0001f50d <b>Scan Token</b> — audits, wallet & deployer checks, portfolio\n"
        f"\U0001f4c8 <b>Market Intel</b> — trending, prices, sentiment, news\n"
        f"\U0001f4ac <b>AI Chat</b> — RAG-powered answers on crypto & code\n"
        f"\U0001f5fa\ufe0f <b>RugMaps</b> — wallet cluster bubble maps\n"
        f"\U0001f4c9 <b>RugCharts</b> — chart images & technical analysis\n"
        f"\u2139\ufe0f <b>Help</b> — full command reference\n\n"
        f"\U0001f4f3 <b>Free:</b> {TIERS['free']['scans_per_week']} scans + "
        f"{TIERS['free']['ai_msgs_per_week']} AI msgs/week\n"
        f"{BETA_BANNER}\n\n"
        f"<b>Quick Start:</b>\n"
        f"\U0001f4cc Paste any contract address \u2192 auto-scan\n"
        f"\U0001f4cc Type <code>$TOKEN</code> \u2192 instant price\n"
        f"\U0001f4cc /chat <code>your question</code> \u2192 talk to me\n"
        f"\U0001f4cc Pick a category below \U0001f447"
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


def format_scoring_breakdown(breakdown: dict) -> str:
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


def format_funding_analysis(r: dict) -> str:
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


# --- SENTINEL deep forensic flags (v2 /scan + deep scan) ---

# Known blue-chip allowlist — imported lazily from the unified scanner
# scoring module (app/domains/scanner/unified/scoring.py) so the bot never
# hardcodes token addresses. Blue-chips (WETH, WBTC, USDC, USDT, DAI, stETH)
# legitimately carry high HHI / low LP-lock signals; those warnings are
# suppressed in /scan output (Bug 1 in scoring.py).
_BLUECHIP_CACHE: frozenset[str] | None = None


def _bluechip_addresses() -> frozenset[str]:
    global _BLUECHIP_CACHE
    if _BLUECHIP_CACHE is None:
        try:
            from app.domains.scanner.unified.scoring import BLUECHIP_ADDRESSES

            _BLUECHIP_CACHE = frozenset(a.lower() for a in BLUECHIP_ADDRESSES)
        except ImportError:  # pragma: no cover - defensive
            _BLUECHIP_CACHE = frozenset()
    return _BLUECHIP_CACHE


def is_bluechip(address: str) -> bool:
    """True for known blue-chip tokens (case-insensitive allowlist)."""
    return (address or "").strip().lower() in _bluechip_addresses()


def _sentinel_free(sentinel: dict | None) -> dict:
    """Extract the free-module bucket from a v2 sentinel dict (best-effort)."""
    if not isinstance(sentinel, dict):
        return {}
    free = sentinel.get("free")
    return free if isinstance(free, dict) else {}


def _module_warnings(free: dict, *keys: str) -> list[str]:
    """Collect warnings across the given sentinel free modules (deduped)."""
    seen: list[str] = []
    for key in keys:
        mod = free.get(key)
        if isinstance(mod, dict):
            for w in mod.get("warnings") or []:
                w_str = str(w).strip()
                if w_str and w_str not in seen:
                    seen.append(w_str)
    return seen


def format_sentinel_flags(r: dict) -> str:
    """Render SENTINEL free-tier flags for the /scan report.

    Reads the v2 scanner's ``sentinel`` dict (free/premium module buckets).
    Blue-chip tokens (WETH, WBTC, ...) legitimately trigger concentration /
    low-lock signals — those warnings are suppressed and replaced by a single
    green confirmation line. Returns "" when the v2 response carried no
    sentinel data.
    """
    sentinel = r.get("sentinel")
    if not isinstance(sentinel, dict):
        return ""
    d = thin_sep()
    if is_bluechip(r.get("address", "")):
        return (
            "\n\U0001f6e1\ufe0f <b>SENTINEL</b>\n"
            f"{d}\n"
            "  \u2705 <b>Known blue-chip</b> \u2014 concentration & lock signals suppressed"
        )
    free = _sentinel_free(sentinel)
    flags: list[str] = []
    if free:
        # Holder HHI concentration
        ha = free.get("holder_analysis")
        if isinstance(ha, dict):
            hhi = ha.get("hhi")
            level = str(ha.get("risk_level") or "").lower()
            if hhi is not None:
                try:
                    hhi_f = float(hhi)
                except (TypeError, ValueError):
                    hhi_f = 0.0
                if level in ("high", "critical"):
                    flags.append(f"\u26a0\ufe0f High holder concentration (HHI: {hhi_f:.0f})")
                else:
                    flags.append(f"\U0001f465 Holder concentration (HHI: {hhi_f:.0f}, {level})")
        # LP lock
        lv = free.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            try:
                locked_f = float(lv["total_locked_percentage"])
            except (TypeError, ValueError):
                locked_f = 0.0
            flags.append(f"\U0001f510 LP Lock: {locked_f:.0f}%")
        # Contract authority
        ca = free.get("contract_authority")
        if isinstance(ca, dict):
            ca_level = str(ca.get("risk_level") or "LOW").upper()
            if ca_level in ("HIGH", "CRITICAL"):
                flags.append(f"\U0001f512 Contract authority risk: {ca_level}")
        # Proxy
        px = free.get("proxy_detection")
        if isinstance(px, dict):
            if px.get("can_upgrade"):
                flags.append("\u26a0\ufe0f Upgradeable proxy (admin can change implementation)")
            elif px.get("is_proxy"):
                flags.append(f"\U0001f4e6 Proxy contract ({px.get('proxy_type') or 'unknown'})")
        # Deployer reputation
        dr = free.get("dev_reputation")
        if isinstance(dr, dict):
            rep = str(dr.get("reputation_level") or "").lower()
            if rep in ("suspicious", "dangerous"):
                score = dr.get("reputation_score")
                score_str = f"{int(score)}/100" if score is not None else "N/A"
                flags.append(f"\U0001f468\u200d\U0001f4bb Dev reputation: {rep} ({score_str})")
        # Remaining module warnings (deduped, capped)
        for w in _module_warnings(
            free,
            "holder_analysis",
            "liquidity_verification",
            "contract_authority",
            "proxy_detection",
            "metadata_fingerprint",
            "dev_reputation",
        ):
            if len(flags) >= 6:
                break
            flags.append(f"\u26a0\ufe0f {w[:100]}")
    if not flags:
        flags.append("\u2705 No SENTINEL red flags")
    lines = ["", "\U0001f6e1\ufe0f <b>SENTINEL</b>", d]
    lines.extend(f"  {f}" for f in flags)
    return "\n".join(lines)


# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---

SENTINEL_MODULE_EMOJI: dict[str, str] = {
    "holder_analysis": "\U0001f465",  # 👥
    "liquidity_verification": "\U0001f510",  # 🔐
    "dev_reputation": "\U0001f468\u200d\U0001f4bb",  # 👨‍💻
    "metadata_fingerprint": "\U0001f5c2\ufe0f",  # 🗒️
    "contract_authority": "\U0001f512",  # 🔒
    "proxy_detection": "\U0001f4e6",  # 📦
    "bundle_detection": "\U0001f4e6",  # 📦
    "exchange_funding": "\U0001f3e6",  # 🏦
    "wash_trading": "\U0001f504",  # 🔄
    "honeypot_detection": "\U0001f3ef",  # 🏯
    "sentiment": "\U0001f4ac",  # 💬
    "pumpfun_analysis": "\U0001f680",  # 🚀
    "mev_detection": "\U0001f50d",  # 🔍
    "flash_loan_detection": "\u26a1",  # ⚡
    "pump_dump_detection": "\U0001f4c8",  # 📈
    "oracle_manipulation": "\U0001f4b0",  # 💰
    "governance_attack": "\U0001f3db\ufe0f",  # 🏛️
    "static_analysis": "\U0001f50e",  # 🔎
    "address_labels": "\U0001f3f7\ufe0f",  # 🏷️
    "block_zero_sniper": "\U0001f3af",  # 🎯
    "gas_trace": "\u26a1",  # ⚡
    "cross_chain_trace": "\U0001f517",  # 🔗
}


def _risk_level_emoji(level: str) -> str:
    lvl = str(level or "").lower()
    if lvl in ("critical", "dangerous"):
        return "\U0001f534"  # 🔴
    if lvl in ("high", "suspicious"):
        return "\U0001f7e1"  # 🟠
    if lvl in ("medium", "moderate"):
        return "\U0001f7e0"  # 🟡
    return "\U0001f7e2"  # 🟢


def _sentinel_recommendations(risk: float, level: str, red_flags: list) -> list[str]:
    """Build DYOR recommendations from sentinel risk signals."""
    lvl = str(level or "").lower()
    recs: list[str] = []
    if risk >= 70 or lvl in ("critical", "high"):
        recs.append("\U0001f534 Avoid this token \u2014 critical risk signals detected")
    elif risk >= 40:
        recs.append("\U0001f7e1 Proceed with extreme caution \u2014 elevated risk")
    else:
        recs.append("\U0001f7e2 Low risk \u2014 standard DeFi caution still applies")
    joined = " ".join(str(f).lower() for f in red_flags)
    if "honeypot" in joined:
        recs.append("\U0001f3ef Honeypot signal \u2014 verify you can actually SELL before buying")
    if "bundle" in joined:
        recs.append("\U0001f4e6 Bundle activity \u2014 volume & concentration may be fabricated")
    if "wash" in joined:
        recs.append("\U0001f504 Wash trading suspected \u2014 volume may be fake")
    recs.append("\U0001f50d Always verify with a second source: /scan <code>address</code>")
    return recs[:6]


def format_sentinel_deepscan_section(data: dict) -> str:
    """Render the SENTINEL deep-forensic section.

    Consumes the raw response from ``POST /api/v1/x402-tools/sentinel_scan``
    (SentinelReport serialized): composite risk score, per-module risk
    breakdown, red flags, and per-module detail (holder HHI, LP lock,
    contract authority, deployer reputation). Used by /deepscan (appended to
    the base v2 scan report) and /sentinelscan (standalone).
    """
    s = sep()
    d = thin_sep()
    address = data.get("address") or data.get("token_address") or ""
    chain = data.get("chain") or "unknown"
    try:
        risk = float(data.get("composite_risk_score") or 0)
    except (TypeError, ValueError):
        risk = 0.0
    level = str(data.get("risk_level") or "unknown")
    red_flags = data.get("red_flags") or []
    module_scores = data.get("module_risk_scores") or {}
    holder = data.get("holder_analysis") or {}
    liq = data.get("liquidity_verification") or {}
    dev = data.get("dev_reputation") or {}
    authority = data.get("contract_authority") or {}

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>SENTINEL DEEP FORENSIC SCAN</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}\n")

    # ── Composite risk assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})")
    lines.append("")

    # ── Key findings (red flags) ──
    if red_flags:
        lines.append("\U0001f6a8 <b>Key Findings</b>")
        lines.append(d)
        for flag in red_flags[:8]:
            lines.append(f"  \U0001f534 {str(flag)[:160]}")
        lines.append("")
    else:
        lines.append("\U0001f7e2 <b>No critical findings</b>")
        lines.append("")

    # ── Module risk breakdown ──
    if module_scores:
        lines.append("\U0001f4ca <b>Module Risk Breakdown</b>")
        lines.append(d)
        ranked = sorted(
            module_scores.items(),
            key=lambda kv: float(kv[1] or 0),
            reverse=True,
        )
        for mod, score in ranked[:10]:
            try:
                mscore = float(score or 0)
            except (TypeError, ValueError):
                mscore = 0.0
            memoji = SENTINEL_MODULE_EMOJI.get(mod, "\U0001f539")  # 🔹
            filled = max(0, min(10, int(mscore / 10)))
            mbar = "\u2b1b" * filled + "\u2b1c" * (10 - filled)
            lines.append(f"  {memoji} {mod.replace('_', ' ').title()}: {mscore:.0f}/100")
            lines.append(f"     <code>{mbar}</code>")
        lines.append("")

    # ── Holder analysis ──
    if holder:
        lines.append("\U0001f465 <b>Holder Analysis</b>")
        lines.append(d)
        hhi = holder.get("hhi")
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            hhi_emoji = (
                "\U0001f534" if str(holder.get("risk_level") or "").lower() in ("high", "critical") else "\U0001f7e2"
            )
            lines.append(f"  {hhi_emoji} HHI Concentration: <b>{hhi_f:.0f}</b> ({holder.get('risk_level', 'unknown')})")
        if holder.get("total_holders") is not None:
            try:
                lines.append(f"  \U0001f465 Total Holders: <b>{int(holder['total_holders']):,}</b>")
            except (TypeError, ValueError):
                pass
        if holder.get("top_10_percentage") is not None:
            try:
                lines.append(f"  \U0001f40b Top-10 Supply: <b>{float(holder['top_10_percentage']):.0f}%</b>")
            except (TypeError, ValueError):
                pass
        for w in (holder.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── LP analysis ──
    if liq:
        lines.append("\U0001f510 <b>LP Analysis</b>")
        lines.append(d)
        locked = liq.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            lock_emoji = "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            lines.append(f"  {lock_emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        if liq.get("lp_burned"):
            lines.append("  \U0001f525 LP tokens burned")
        if liq.get("fake_lock_detected"):
            lines.append("  \U0001f534 Fake lock detected")
        for w in (liq.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Contract authority ──
    if authority:
        lines.append("\U0001f512 <b>Contract Authority</b>")
        lines.append(d)
        ca_level = str(authority.get("risk_level") or "LOW").upper()
        lines.append(f"  {_risk_level_emoji(ca_level)} Authority Risk: <b>{ca_level}</b>")
        for w in (authority.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Deployer reputation ──
    if dev:
        lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer Reputation</b>")
        lines.append(d)
        rep_score = dev.get("reputation_score")
        rep_level = str(dev.get("reputation_level") or "unknown")
        if rep_score is not None:
            try:
                lines.append(
                    f"  {_risk_level_emoji(rep_level)} Reputation: <b>{int(rep_score)}/100</b> ({rep_level.title()})"
                )
            except (TypeError, ValueError):
                pass
        if dev.get("total_launches") is not None:
            try:
                lines.append(
                    f"  \U0001f4e6 Launches: <b>{int(dev['total_launches'])}</b> | "
                    f"Rugged: <b>{int(dev.get('rugged_count') or 0)}</b>"
                )
            except (TypeError, ValueError):
                pass
        rug_rate = dev.get("rug_rate")
        if rug_rate is not None:
            try:
                lines.append(f"  \U0001f4a5 Rug Rate: <b>{float(rug_rate) * 100:.0f}%</b>")
            except (TypeError, ValueError):
                pass
        for w in (dev.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Recommendations ──
    lines.append("\U0001f4a1 <b>Recommendations</b>")
    lines.append(d)
    lines.extend(f"  {rec}" for rec in _sentinel_recommendations(risk, level, red_flags))
    lines.append("")
    lines.append(
        "\u26a0\ufe0f <i>Composite risk is 0-100 (higher = riskier). "
        "SENTINEL data is forensic evidence \u2014 always DYOR.</i>"
    )
    lines.append(footer_links())
    return "\n".join(lines)


def format_sentinel_scan_report(data: dict) -> str:
    """Standalone /sentinelscan report — SENTINEL deep scan only."""
    return format_sentinel_deepscan_section(data)


# --- Scan-report intel helpers (own-data engines) ---


def _bundle_alert_line(r: dict) -> str | None:
    """Red alert banner when the own bundle-launch engine flags a bundled launch.

    Reads ``r["bundle_analysis"]`` (``is_bundled`` / ``confidence`` /
    ``risk_label``) and returns a single prominent banner line, or ``None``
    when the scanner found no coordinated launch (or no bundle data).
    """
    ba = r.get("bundle_analysis")
    if not isinstance(ba, dict) or not ba.get("is_bundled"):
        return None
    try:
        conf = float(ba.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    risk = str(ba.get("risk_label") or "unknown").upper()
    return f"\U0001f6a8 <b>BUNDLED LAUNCH DETECTED</b> \u2014 {conf:.0f}% confidence, {risk}"


def _deployer_history_line(r: dict) -> str | None:
    """Repeat-deployer track record line, or ``None`` when no profile exists.

    Reads ``r["deployer_profile"]`` (own ``deployer_profiles`` engine) which
    carries ``total_tokens_launched`` / ``total_deployments``, ``rugged_pct``
    (0-1 fraction), and ``avg_lifespan_days``. Falls back to the SENTINEL
    deep-profile ``rug_rate`` (already a percentage) when only that is set.
    """
    dp = r.get("deployer_profile")
    if not isinstance(dp, dict) or not dp:
        return None
    tokens = int(dp.get("total_tokens_launched") or dp.get("total_deployments") or 0)
    rugged_raw = dp.get("rugged_pct")
    if rugged_raw is None:
        rugged_raw = dp.get("rug_rate", 0.0)
    try:
        rugged_f = float(rugged_raw or 0.0)
    except (TypeError, ValueError):
        rugged_f = 0.0
    rugged_pct = round(rugged_f * 100) if rugged_f <= 1.0 else round(rugged_f)
    life_days = dp.get("avg_lifespan_days")
    if life_days is None:
        life_str = "unknown"
    else:
        try:
            life_f = float(life_days)
        except (TypeError, ValueError):
            life_f = 0.0
        life_str = f"{round(life_f)} days" if life_f >= 1 else f"{round(life_f * 24)} hours"
    return f"\U0001f464 <b>Repeat Deployer</b>: {tokens} tokens, {rugged_pct}% rugged, avg life {life_str}"


def _wallet_freshness_line(r: dict) -> str | None:
    """Wallet freshness + CEX-funding line, or ``None`` when no metrics.

    Reads ``r["wallet_metrics"]`` (``fresh_wallet_pct`` / ``cex_funded_pct``)
    and returns a compact line. Skips the graceful-degradation payload
    (``result == "unavailable"``) so a failed engine never renders zeros.
    """
    wm = r.get("wallet_metrics")
    if not isinstance(wm, dict) or wm.get("result") == "unavailable":
        return None
    try:
        fresh = float(wm.get("fresh_wallet_pct") or 0)
        cex = float(wm.get("cex_funded_pct") or 0)
    except (TypeError, ValueError):
        return None
    return f"\U0001f195 {fresh:.0f}% fresh wallets (<30 days), \U0001f3e6 {cex:.0f}% CEX-funded"


# --- Format scan report ---
def format_scan_report(r: dict) -> str:
    s = sep()
    d = thin_sep()
    age_str = ""
    if r.get("created_at"):
        try:
            created = datetime.fromtimestamp(r["created_at"] / 1000, tz=UTC)
            age = datetime.now(UTC) - created
            if age.days > 365:
                age_str = f" | Age: {age.days // 365}y {age.days % 365 // 30}mo"
            elif age.days > 0:
                age_str = f" | Age: {age.days}d"
            else:
                age_str = f" | Age: {age.seconds // 3600}h"
        except (ConnectionError, OSError, TimeoutError):
            pass
    chain_str = fmt_chain(r["chain"])
    lines = [
        "\U0001f6e1\ufe0f <b>RMI Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
    ]
    bundle_alert = _bundle_alert_line(r)
    if bundle_alert:
        lines.append(bundle_alert)
    lines.extend(
        [
            "",
            "\U0001f4ca <b>Market Data</b>",
            d,
            f"\U0001f4b0 Price: <b>{fmt_number(r['price'])}</b>",
            f"\U0001f4c8 Market Cap: <b>{fmt_number(r['mcap'])}</b>",
            f"\U0001f4a7 Liquidity: <b>{fmt_number(r['liquidity'])}</b>",
            f"\U0001f4ca 24h Volume: <b>{fmt_number(r['volume_24h'])}</b>",
            f"\U0001f504 1h: {fmt_pct(r['price_change_1h'])} | 24h: {fmt_pct(r['price_change_24h'])} | 7d: {fmt_pct(r['price_change_7d'])}",
            "",
            "\U0001f512 <b>Security</b>",
            d,
            risk_bar(r["risk_score"]),
        ]
    )
    if r.get("safety_score") is None:
        # Legacy scan: threats are listed here. v2 scans surface them as
        # "Red Flags" in the Explainable Scoring block below.
        if r["threats"]:
            lines.append("")
            for t in r["threats"]:
                lines.append(f"  \U0001f534 {t}")
        else:
            lines.append("  \u2705 No threats detected")
    cl = []
    if r["honeypot"] is not None:
        cl.append(threat_indicator("Honeypot", r["honeypot"]))
    if r["contract_verified"] is not None:
        cl.append(threat_indicator("Verified", not r["contract_verified"]))
    if r["mintable"] is not None:
        cl.append(threat_indicator("Mintable", r["mintable"]))
    if r["owner_renounced"] is not None:
        cl.append(threat_indicator("Ownership Renounced", not r["owner_renounced"]))
    if r["lp_locked"] is not None:
        cl.append(f"\U0001f510 LP Locked: {r['lp_locked'] * 100:.0f}%")
    if r["buy_tax"] is not None:
        cl.append(f"\U0001f4e5 Buy Tax: {r['buy_tax'] * 100:.1f}%")
    if r["sell_tax"] is not None:
        cl.append(f"\U0001f4e4 Sell Tax: {r['sell_tax'] * 100:.1f}%")
    if cl:
        lines.append("")
        lines.extend(cl)
    if r["top_holders"]:
        lines.extend(["", "\U0001f465 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " \U0001f4c4" if h.get("is_contract") else ""
            lines.append(f"  {i}. <code>{h['address']}</code> - {h['pct']:.1f}%{tag}{cf}")
    adv = []
    has_bundle_alert = bundle_alert is not None
    if r["bundle_detected"] and not has_bundle_alert:
        adv.append("\U0001f578\ufe0f Bundle activity detected")
    # Richer own-engine wallet metrics (fresh % + CEX %) replace the legacy
    # fresh-wallet line when present, so we never double-report freshness.
    wallet_line = _wallet_freshness_line(r)
    if wallet_line:
        adv.append(wallet_line)
    elif r["fresh_wallet_pct"] > 0:
        adv.append(f"\U0001f195 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    deployer_line = _deployer_history_line(r)
    if deployer_line:
        adv.append(deployer_line)
    if r["volume_real_ratio"] is not None:
        adv.append(f"\U0001f4ca Vol/MCap ratio: {r['volume_real_ratio']:.2f}x")
    if r["dev_wallet"]:
        adv.append(f"\U0001f468\u200d\U0001f4bb Dev: <code>{r['dev_wallet']}</code>")
    if adv:
        lines.extend(["", "\U0001f52c <b>Advanced Analysis</b>", d])
        lines.extend(adv)
    socials = r.get("socials", {})
    if socials:
        soc_parts = []
        for stype, surl in socials.items():
            if stype == "twitter":
                soc_parts.append(f'<a href="{surl}">\U0001d54f</a>')
            elif stype == "telegram":
                soc_parts.append(f'<a href="{surl}">\U0001f4f1</a>')
            elif stype == "website":
                soc_parts.append(f'<a href="{surl}">\U0001f310</a>')
        if soc_parts:
            lines.extend(["", f"\U0001f517 Socials: {' | '.join(soc_parts)}"])
    # v2 explainable scoring: safety/confidence/risk-level + green/red flags
    v2 = []
    if r.get("safety_score") is not None:
        emoji = "\U0001f7e2" if r["safety_score"] >= 80 else "\U0001f7e1" if r["safety_score"] >= 40 else "\U0001f534"
        v2.append(f"{emoji} Safety Score: <b>{r['safety_score']:.0f}/100</b>")
    if r.get("confidence") is not None:
        v2.append(f"\U0001f50d Confidence: <b>{r['confidence']:.0f}%</b>")
    if r.get("risk_level"):
        v2.append(f"\U0001f6a8 Risk Level: <b>{r['risk_level'].upper()}</b>")
    if r.get("green_flags"):
        gfs = ", ".join(str(g) for g in r["green_flags"][:5])
        v2.append(f"\U0001f7e2 Green Flags: {gfs}")
    if r.get("safety_score") is not None and r.get("threats"):
        rfs = ", ".join(str(t) for t in r["threats"][:6])
        v2.append(f"\U0001f534 Red Flags: {rfs}")
    if v2:
        lines.extend(["", "\U0001f9e9 <b>Explainable Scoring</b>", d])
        lines.extend(v2)
    if r.get("breakdown"):
        lines.append(format_scoring_breakdown(r["breakdown"]))
    # ── SENTINEL free-tier flags (HHI, LP lock, authority, proxy, dev rep) ──
    # Blue-chip tokens are suppressed (concentration/lock signals are normal).
    sentinel_block = format_sentinel_flags(r)
    if sentinel_block:
        lines.extend(sentinel_block.split("\n"))
    # ── News card (Ground News style), fall back to plain sentiment ──
    news_block = format_news_card(r.get("news_card"))
    if not news_block:
        news_block = format_news_sentiment(r.get("news_sentiment"))
    if news_block:
        lines.extend(news_block)

    lines.extend(["", s, "\U0001f4b1 <b>Trade:</b>"])
    lines.append(footer_links())
    return "\n".join(lines)


def format_news_card(nc: dict[str, Any] | None) -> list[str]:
    """Format a Ground News-style news card into scan-report lines.

    Accepts the engine payload from ``get_token_news_card``:

    .. code-block:: text

        📰 News Sentiment: BEARISH 🔴 (72 trending)
        ────────────────────────
        ├─ CoinDesk: "Coldcard exploit..." [VERIFIED] [HIGH IMPACT]
        ├─ TheBlock: "Bitcoin wallets drained..." [REPORTED]
        └─ 3 sources corroborate · on-chain evidence ✓

    Returns an empty list when there is no usable data so callers can
    skip the block entirely.
    """
    if not isinstance(nc, dict):
        return []
    articles = nc.get("articles")
    if not isinstance(articles, list) or not articles:
        return []

    sentiment = str(nc.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    trending = int(nc.get("trending_score") or 0)

    lines = [
        "",
        f"\U0001f4f0 News Sentiment: <b>{sentiment}</b> {emoji} ({trending} trending)",
        thin_sep(),
    ]
    for art in articles[:5]:
        source = str(art.get("source") or "Unknown")
        raw_title = str(art.get("title") or "").strip()
        title = raw_title[:60] + ("..." if len(raw_title) > 60 else "")
        factuality = str(art.get("factuality") or "").upper()
        impact = str(art.get("market_impact") or "").upper()
        tags = []
        if factuality in ("VERIFIED", "REPORTED", "OPINION", "RUMOR"):
            tags.append(f"[{factuality}]")
        if impact:
            tags.append(f"[{impact} IMPACT]")
        line = f'\u251c\u2500 {source}: "{title}"'
        if tags:
            line += " " + " ".join(tags)
        lines.append(line)

    corr = nc.get("corroboration") or {}
    if isinstance(corr, dict):
        total = int(corr.get("total_sources") or 0)
        on_chain = bool(corr.get("on_chain_evidence"))
        if total >= 2 or on_chain:
            corr_line = f"\u2514\u2500 {total} sources corroborate"
            if on_chain:
                corr_line += " \u00b7 on-chain evidence \u2713"
            lines.append(corr_line)
    return lines


def format_news_sentiment(ns) -> list[str]:
    """Format a news_sentiment dict into scan-report lines (empty when absent).

    Accepts the engine payload from ``analyze_token_news_sentiment`` (or a
    dict with the same keys). Returns an empty list when there is no usable
    data so callers can skip the block entirely.
    """
    if not isinstance(ns, dict):
        return []
    if not ns.get("article_count"):
        return []
    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    lines = [
        "",
        "\U0001f4f0 <b>News Sentiment</b>",
        thin_sep(),
        f"{emoji} Sentiment: <b>{sentiment}</b> (confidence {float(ns.get('confidence') or 0) * 100:.0f}%)",
        f"\U0001f4f0 Articles: {int(ns.get('article_count') or 0)}",
    ]
    breakdown = ns.get("sentiment_breakdown") or {}
    if isinstance(breakdown, dict):
        lines.append(
            f"\U0001f4c8 Breakdown: \U0001f7e2{breakdown.get('positive', 0)} "
            f"\U0001f534{breakdown.get('negative', 0)} "
            f"\U0001f7e1{breakdown.get('neutral', 0)}"
        )
    if ns.get("trending_score") is not None:
        lines.append(f"\U0001f525 Media Attention: <b>{int(ns['trending_score'])}/100</b>")
    flags = ns.get("flags") or []
    if isinstance(flags, list) and flags:
        lines.append(f"\U0001f3c6 {', '.join(str(f) for f in flags[:3])}")
    return lines


# --- Wallet report formatting ---
def _activity_score(tx_count: int | None) -> str:
    if not tx_count or tx_count == 0:
        return "\U0001f7e0 None"
    if tx_count < 10:
        return "\U0001f7e1 Low"
    if tx_count < 100:
        return "\U0001f7e2 Medium"
    if tx_count < 1000:
        return "\U0001f535 High"
    return "\U0001f48e Very High"


def _explorer_link(address: str, chain: str) -> str:
    url = explorer_url(address, chain)
    if chain.lower() == "solana":
        return f'<a href="{url}">\U0001f50d Solscan</a>'
    domain = url.split("//")[1].split("/")[0]
    return f'<a href="{url}">\U0001f50d {domain.split(".")[0].title()}</a>'


def format_wallet_report(r: dict) -> str:
    s = sep()
    d = thin_sep()
    age_str = f"{r['wallet_age_days']} days" if r["wallet_age_days"] else "Unknown"
    tx_count = r.get("tx_count")
    tx_str = (
        f"\U0001f4dd Transactions: <b>{tx_count:,}</b>"
        if tx_count is not None and tx_count > 0
        else "\U0001f4dd Transactions: N/A"
    )
    score_str = _activity_score(tx_count)
    explorer_str = _explorer_link(r["address"], r["chain"])
    lines = [
        "\U0001f9db <b>Wallet Analysis</b>",
        s,
        f"<code>{r['address']}</code>",
        f"{fmt_chain(r['chain'])} | Age: {age_str} | {explorer_str}",
        "",
        "\U0001f4ca <b>Overview</b>",
        d,
        f"\U0001f4b0 Balance: <b>{fmt_number(r['balance_usd'])}</b>",
        tx_str,
        f"\U0001f4ca Activity Score: {score_str}",
        f"\U0001f3f7\ufe0f Type: {'Exchange' if r['is_exchange'] else 'Contract' if r['is_contract'] else 'EOA (Wallet)'}",
    ]
    if r["is_fresh"]:
        lines.append("\U0001f195 <b>FRESH WALLET</b> (<7 days) - \u26a0\ufe0f Higher risk")
    if r["flags"]:
        lines.extend(["", "\u26a0\ufe0f <b>Flags</b>", d])
        for flag in r["flags"]:
            lines.append(f"  \U0001f534 {flag}")
    if r["pnl_estimate"] is not None:
        pnl_e = "\U0001f4c8" if r["pnl_estimate"] >= 0 else "\U0001f4c9"
        lines.extend(["", f"{pnl_e} <b>Estimated P&L:</b> {fmt_number(r['pnl_estimate'])}"])
    if r["top_tokens"]:
        lines.extend(["", "\U0001fa99 <b>Top Holdings</b>", d])
        for i, t in enumerate(r["top_tokens"][:5], 1):
            lines.append(f"  {i}. <b>{t.get('symbol', '???')}</b> - {fmt_number(t.get('value_usd', 0))}")
    if r["funding_source"]:
        lines.extend(["", f"\U0001f3e6 <b>Funding Source:</b> {r['funding_source']}"])
    if r["connected_wallets"]:
        lines.extend(["", "\U0001f517 <b>Connected Wallets</b>", d])
        for w in r["connected_wallets"][:5]:
            lines.append(f"  \u2022 <code>{w}</code>")
    if r["risk_score"] > 0:
        lines.extend(["", risk_bar(r["risk_score"])])

    # v2 wallet metrics: entity, scam exposure, smart money overlap
    v2_lines = []
    if r.get("entity_name"):
        v2_lines.append(f"\U0001f3e6 Entity: <b>{r['entity_name']}</b> ({r.get('entity_type', 'unknown')})")
    if r.get("persona"):
        conf = f" ({r.get('persona_confidence', 0)}% conf)" if r.get("persona_confidence") else ""
        v2_lines.append(f"\U0001f9d0 Persona: {r['persona']}{conf}")
    if r.get("scam_exposure_pct"):
        emoji = (
            "\U0001f534"
            if r["scam_exposure_pct"] > 20
            else "\U0001f7e1"
            if r["scam_exposure_pct"] > 5
            else "\U0001f7e2"
        )
        v2_lines.append(
            f"{emoji} Scam Exposure: <b>{r['scam_exposure_pct']:.0f}%</b> "
            f"({r.get('scam_tokens_held', 0)} scam token(s) held)"
        )
    if r.get("smart_money_overlap"):
        sm = ", ".join(str(s) for s in r["smart_money_overlap"][:5])
        v2_lines.append(f"\U0001f4b0 Smart Money Overlap: {sm}")
    if r.get("behavior_patterns"):
        bp = ", ".join(str(b) for b in r["behavior_patterns"][:4])
        v2_lines.append(f"\U0001f50e Behavior: {bp}")
    if r.get("risk_forecast"):
        v2_lines.append(f"\U0001f52e Forecast: {r['risk_forecast'][:120]}")
    if v2_lines:
        lines.extend(["", "\U0001f9e9 <b>v2 Wallet Intel</b>", d])
        lines.extend(v2_lines)

    funding_text = format_funding_analysis(r)
    if funding_text:
        lines.append(funding_text)

    lines.append(footer_links())
    return "\n".join(lines)
