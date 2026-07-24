#!/usr/bin/env python3
"""
RugMunchBot -- Message formatting & utility helpers.

All formatting, text generation, and small utility functions
used by handlers and other modules.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

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

# --- Address detection helpers ---
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")

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

def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)
    return (
        f"\u26a1 <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"\U0001f4ca <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"\U0001f381 <b>Bonus Balance:</b> {bonus} extra\n\n"
        f"<b>Unlock more power:</b>\n\n"
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
            lines.append(f"  {i}. <code>{short_addr(h['address'])}</code> - {h['pct']:.1f}%{tag}{cf}")
    adv = []
    if r["bundle_detected"]:
        adv.append("\U0001f578\ufe0f Bundle activity detected")
    if r["fresh_wallet_pct"] > 0:
        adv.append(f"\U0001f195 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    if r["volume_real_ratio"] is not None:
        adv.append(f"\U0001f4ca Vol/MCap ratio: {r['volume_real_ratio']:.2f}x")
    if r["dev_wallet"]:
        adv.append(f"\U0001f468\u200d\U0001f4bb Dev: <code>{short_addr(r['dev_wallet'])}</code>")
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
    lines.extend(["", s, "\U0001f4b1 <b>Trade:</b>"])
    lines.append(footer_links())
    return "\n".join(lines)

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
    if chain.lower() == "solana":
        return f'<a href="https://solscan.io/account/{address}">\U0001f50d Solscan</a>'
    explorers = {
        "ethereum": "etherscan.io",
        "bsc": "bscscan.com",
        "polygon": "polygonscan.com",
        "arbitrum": "arbiscan.io",
        "optimism": "optimistic.etherscan.io",
        "base": "basescan.org",
        "avalanche": "snowtrace.io",
        "fantom": "ftmscan.com",
    }
    domain = explorers.get(chain.lower(), "etherscan.io")
    return f'<a href="https://{domain}/address/{address}">\U0001f50d {domain.split(".")[0].title()}</a>'

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
            lines.append(f"  \u2022 <code>{short_addr(w)}</code>")
    if r["risk_score"] > 0:
        lines.extend(["", risk_bar(r["risk_score"])])
    lines.append(footer_links())
    return "\n".join(lines)
