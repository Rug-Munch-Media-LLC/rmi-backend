#!/usr/bin/env python3
"""
CryptoRugMunch Bot v6 - The Bloomberg Terminal of Shitcoins
============================================================
Production-grade: multi-chain scanning, wallet forensics, tiered subs,
Telegram Stars payments, DEX ref revenue, referral system, AI chat,
inline queries, scheduled tips, trending tokens, spam protection,
website integration, social links, bot profile setup.

Run: cd /root/backend && python3 -m app.domains.telegram.rugmunchbot.bot
"""

import asyncio

# ── Local imports ──
import contextlib
import logging
import random
import re
import secrets
import sys
import time
from datetime import UTC, datetime, time as dtime
from typing import Any
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LabeledPrice,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db

# ── Command module imports ──
from app.domains.telegram.rugmunchbot.commands.chart import cmd_chart
from app.domains.telegram.rugmunchbot.commands.portfolio import cmd_portfolio
from app.domains.telegram.rugmunchbot.commands.rugmap import cmd_rugmap
from app.domains.telegram.rugmunchbot.commands.simulate import cmd_simulate
from app.domains.telegram.rugmunchbot.commands.smartmoney import cmd_smartmoney
from app.domains.telegram.rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,
    BACKEND_URL,
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    BOT_TOKEN,
    BOT_USERNAME,
    BUY_STARS_PACKS,
    CHAIN_IDS,
    CHANNELS,
    DEX_REF_LINKS,
    DEXSCREENER,
    GOPLUS,
    HONEYPOT,
    OWNER_IDS,
    SCAM_SCHOOL_TOPICS,
    SCAN_PACKS,
    SUPPORT_EMAIL,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    TWITTER_URL,
    WEB_APP_URL,
    WEBSITE_URL,
    check_backend_health,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
    threat_indicator,
)

# ══════════════════════════════════════════════
# SOL PRICE CACHE
# ══════════════════════════════════════════════
_sol_price: float = 150.0  # fallback
_sol_price_ts: float = 0


async def _get_sol_price() -> float:
    """Fetch current SOL/USD price from Jupiter API (free, no auth needed)."""
    global _sol_price, _sol_price_ts
    now = time.time()
    if now - _sol_price_ts < 300:  # cache for 5 minutes
        return _sol_price
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                "https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112"
            )
            if r.status_code == 200:
                data = r.json()
                price_str = (
                    data.get("data", {})
                    .get("So11111111111111111111111111111111111111112", {})
                    .get("price")
                )
                if price_str:
                    _sol_price = float(price_str)
                    _sol_price_ts = now
    except Exception as e:
        logger.warning(f"Failed to fetch SOL price: {e}")
    return _sol_price


# ══════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = get_logger("rmi_bot")

# ══════════════════════════════════════════════
# SPAM PROTECTION
# ══════════════════════════════════════════════
_user_last_action: TTLCache = TTLCache(maxsize=10000, ttl=60)
SPAM_COOLDOWN = 3

# ── Alert rate limiter (max 1 DM per user per 5 min) ──
_alert_last_sent: dict[int, float] = {}
ALERT_RATE_LIMIT_SEC = 300


def _check_alert_rate_limit(user_id: int) -> bool:
    """Return True if user can receive another alert (rate-limited to 1 per 5 min)."""
    now = time.time()
    last = _alert_last_sent.get(user_id, 0)
    if now - last < ALERT_RATE_LIMIT_SEC:
        return False
    _alert_last_sent[user_id] = now
    return True


# ── Pending deep scan payments (token → details) ──
_pending_deep_scans: dict[str, dict] = {}


def check_spam(user_id: int) -> bool:
    now = time.time()
    last = _user_last_action.get(user_id, 0)
    if now - last < SPAM_COOLDOWN and not is_owner(user_id):
        return True
    _user_last_action[user_id] = now
    return False


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")


def is_evm(addr: str) -> bool:
    return bool(EVM_RE.match(addr))


def is_sol(addr: str) -> bool:
    return bool(SOL_RE.match(addr))


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


def sep(char: str = "━", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "─", length: int = 24) -> str:
    return char * length


def footer_links() -> str:
    """Standard footer with RugMunch branding."""
    return f'\n{sep()}\n🔍 RugMunch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    """Button to view full scan on the website."""
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("🌐 Full Report on RugMunch.io", url=url)


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
    """Dynamic social proof from DB stats."""
    try:
        conn = db.get_db()
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
        conn.close()
        users_str = f"{total_users:,}" if total_users else "1,000+"
        scans_str = f"{total_scans:,}" if total_scans else "50,000+"
        return f"👥 {users_str} users | 🔍 {scans_str} scans completed"
    except Exception as e:
        logger.warning(f"social_proof_text DB query failed: {e}")
        return "👥 1,000+ users | 🔍 50,000+ scans completed"


def paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    user = db.get_or_create_user(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    used = scans if action == "scan" else ai
    bonus = user.get("bonus_scans", 0) if action == "scan" else user.get("bonus_ai_msgs", 0)

    return (
        f"⚡ <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"📊 <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n"
        f"🎁 <b>Bonus Balance:</b> {bonus} extra\n\n"
        f"<b>Unlock more power:</b>\n\n"
        f"🔍 <b>Scout ($29.99/mo)</b> - 150 scans/wk + bundle detection\n"
        f"🎯 <b>Hunter ($49.99/mo)</b> - 400 scans/wk + wallet forensics\n"
        f"👑 <b>Pro ($99.99/mo)</b> - 1,000 scans/wk + smart money alerts\n\n"
        f"Or buy one-time top-ups - <b>never expire!</b>\n"
        f"🔋 10 scans = ⭐75 | 50 scans = ⭐300 | 100 scans = ⭐500"
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⭐ View Plans", callback_data="menu_pricing")],
            [InlineKeyboardButton("🔋 Buy Top-Up", callback_data="menu_topup")],
            [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )


# ══════════════════════════════════════════════
# SCAN ENGINE
# ══════════════════════════════════════════════
async def scan_token(address: str, chain: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "name": "Unknown",
        "symbol": "???",
        "price": 0,
        "mcap": 0,
        "fdv": 0,
        "volume_24h": 0,
        "liquidity": 0,
        "price_change_1h": 0,
        "price_change_24h": 0,
        "price_change_7d": 0,
        "holders": 0,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": 0,
        "threats": [],
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # ── DexScreener ──
        try:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                    result["symbol"] = p.get("baseToken", {}).get("symbol", "???")
                    result["price"] = float(p.get("priceUsd", 0))
                    result["mcap"] = float(p.get("marketCap") or p.get("fdv") or 0)
                    result["fdv"] = float(p.get("fdv") or result["mcap"])
                    vol = p.get("volume", {})
                    result["volume_24h"] = float(vol.get("h24") or 0)
                    liq = p.get("liquidity", {})
                    result["liquidity"] = float(liq.get("usd") or 0)
                    pc = p.get("priceChange", {})
                    result["price_change_1h"] = pc.get("h1") or 0
                    result["price_change_24h"] = pc.get("h24") or 0
                    result["price_change_7d"] = pc.get("d7") or 0
                    result["pair_address"] = p.get("pairAddress", "")
                    result["dex"] = p.get("dexId", "")
                    result["chain"] = p.get("chainId", result["chain"])
                    result["created_at"] = p.get("pairCreatedAt")
                    socials = p.get("info", {}).get("socials", [])
                    result["socials"] = {s.get("type"): s.get("url") for s in socials}
        except Exception as e:
            result["errors"].append(f"DexScreener: {e}")

        # ── GoPlus Security (EVM) ──
        if is_evm(address):
            try:
                chain_id_map = {
                    "ethereum": "1",
                    "bsc": "56",
                    "polygon": "137",
                    "arbitrum": "42161",
                    "avalanche": "43114",
                    "base": "8453",
                    "optimism": "10",
                    "fantom": "250",
                }
                cid = chain_id_map.get(result["chain"].lower(), "1")
                r = await client.get(f"{GOPLUS}/token_security/{cid}?contract_addresses={address}")
                if r.status_code == 200:
                    sec = r.json().get("result", {}).get(address.lower(), {})
                    if sec:
                        result["contract_verified"] = sec.get("is_open_source") == "1"
                        result["honeypot"] = sec.get("is_honeypot") == "1"
                        result["buy_tax"] = float(sec.get("buy_tax") or 0)
                        result["sell_tax"] = float(sec.get("sell_tax") or 0)
                        result["mintable"] = sec.get("is_mintable") == "1"
                        result["can_blacklist"] = (
                            sec.get("is_blacklisted") == "1"
                            or sec.get("can_take_back_ownership") == "1"
                        )
                        result["owner_renounced"] = sec.get("is_owner_renounced") != "0"
                        lp_locks = sec.get("lp_holders", [])
                        locked_pct = sum(
                            float(h.get("percent", 0)) for h in lp_locks if h.get("is_locked") == 1
                        )
                        result["lp_locked"] = locked_pct
                        holders_data = sec.get("holders", [])
                        result["holders"] = len(holders_data) if holders_data else 0
                        for h in holders_data[:10]:
                            result["top_holders"].append(
                                {
                                    "address": h.get("address", ""),
                                    "pct": float(h.get("percent") or 0) * 100,
                                    "is_contract": h.get("is_contract") == 1,
                                    "tag": h.get("tag", ""),
                                }
                            )
                        if result["honeypot"]:
                            result["threats"].append("HONEYPOT DETECTED")
                            result["risk_score"] += 40
                        if not result["contract_verified"]:
                            result["threats"].append("Contract not verified")
                            result["risk_score"] += 15
                        if result["mintable"]:
                            result["threats"].append("Mintable supply")
                            result["risk_score"] += 20
                        if result["can_blacklist"]:
                            result["threats"].append("Owner can blacklist")
                            result["risk_score"] += 15
                        if not result["owner_renounced"]:
                            result["threats"].append("Ownership not renounced")
                            result["risk_score"] += 10
                        if result["buy_tax"] and result["buy_tax"] > 0.1:
                            result["threats"].append(
                                f"High buy tax: {result['buy_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 10
                        if result["sell_tax"] and result["sell_tax"] > 0.1:
                            result["threats"].append(
                                f"High sell tax: {result['sell_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 15
                        if locked_pct < 0.5 and result["mcap"] > 10000:
                            result["threats"].append(f"Low LP lock: {locked_pct * 100:.0f}%")
                            result["risk_score"] += 15
            except Exception as e:
                result["errors"].append(f"GoPlus: {e}")

        # ── Honeypot.is (EVM fallback) ──
        if is_evm(address) and result["honeypot"] is None:
            try:
                r = await client.get(f"{HONEYPOT}/IsHoneypot?address={address}")
                if r.status_code == 200:
                    hp = r.json()
                    result["honeypot"] = hp.get("isHoneypot", False)
                    if result["honeypot"]:
                        result["threats"].append("HONEYPOT (honeypot.is)")
                        result["risk_score"] += 40
            except Exception as e:
                result["errors"].append(f"Honeypot.is: {e}")

        # ── RMI Backend ──
        try:
            r = await client.get(f"{BACKEND_URL}/api/v1/databus/token/{address}", timeout=10)
            if r.status_code == 200:
                rmi = r.json()
                if rmi.get("bundle_detected"):
                    result["bundle_detected"] = True
                    result["threats"].append("Bundle activity detected")
                    result["risk_score"] += 20
                if rmi.get("fresh_wallet_pct"):
                    result["fresh_wallet_pct"] = rmi["fresh_wallet_pct"]
                    if rmi["fresh_wallet_pct"] > 50:
                        result["threats"].append(
                            f"High fresh wallet ratio: {rmi['fresh_wallet_pct']:.0f}%"
                        )
                        result["risk_score"] += 10
                if rmi.get("dev_wallet"):
                    result["dev_wallet"] = rmi["dev_wallet"]
        except Exception as e:
            result["errors"].append(f"RMI Backend: {e}")

    # ── Volume authenticity ──
    if result["volume_24h"] > 0 and result["mcap"] > 0:
        ratio = result["volume_24h"] / result["mcap"]
        result["volume_real_ratio"] = ratio
        if ratio > 5:
            result["threats"].append(f"Suspicious volume/mcap ratio: {ratio:.1f}x")
            result["risk_score"] += 15

    # ── Liquidity check ──
    if result["mcap"] > 0 and result["liquidity"] > 0:
        liq_ratio = result["liquidity"] / result["mcap"]
        if liq_ratio < 0.02:
            result["threats"].append("Very low liquidity vs mcap")
            result["risk_score"] += 20

    result["risk_score"] = min(result["risk_score"], 100)
    return result


def format_scan_report(r: dict) -> str:
    chain_str = fmt_chain(r["chain"])
    risk = r["risk_score"]
    d = thin_sep()
    s = sep()

    # Age calculation
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
        except Exception as e:
            logger.warning(f"Failed to parse token age for {r.get('address', '?')}: {e}")

    lines = [
        "🛡️ <b>RMI Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
        "",
        "📊 <b>Market Data</b>",
        d,
        f"💰 Price: <b>{fmt_number(r['price'])}</b>",
        f"📈 Market Cap: <b>{fmt_number(r['mcap'])}</b>",
        f"💧 Liquidity: <b>{fmt_number(r['liquidity'])}</b>",
        f"📊 24h Volume: <b>{fmt_number(r['volume_24h'])}</b>",
        f"🔄 1h: {fmt_pct(r['price_change_1h'])} | 24h: {fmt_pct(r['price_change_24h'])} | 7d: {fmt_pct(r['price_change_7d'])}",
        "",
        "🔒 <b>Security</b>",
        d,
        risk_bar(risk),
    ]

    if r["threats"]:
        lines.append("")
        for t in r["threats"]:
            lines.append(f"  🔴 {t}")
    else:
        lines.append("  ✅ No threats detected")

    # Contract details
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
        cl.append(f"🔐 LP Locked: {r['lp_locked'] * 100:.0f}%")
    if r["buy_tax"] is not None:
        cl.append(f"📥 Buy Tax: {r['buy_tax'] * 100:.1f}%")
    if r["sell_tax"] is not None:
        cl.append(f"📤 Sell Tax: {r['sell_tax'] * 100:.1f}%")
    if cl:
        lines.append("")
        lines.extend(cl)

    # Holders
    if r["top_holders"]:
        lines.extend(["", "👥 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " 📄" if h.get("is_contract") else ""
            lines.append(
                f"  {i}. <code>{short_addr(h['address'])}</code> - {h['pct']:.1f}%{tag}{cf}"
            )

    # Advanced
    adv = []
    if r["bundle_detected"]:
        adv.append("🕸️ Bundle activity detected")
    if r["fresh_wallet_pct"] > 0:
        adv.append(f"🆕 Fresh wallets: {r['fresh_wallet_pct']:.0f}%")
    if r["volume_real_ratio"] is not None:
        adv.append(f"📊 Vol/MCap ratio: {r['volume_real_ratio']:.2f}x")
    if r["dev_wallet"]:
        adv.append(f"👨‍💻 Dev: <code>{short_addr(r['dev_wallet'])}</code>")
    if adv:
        lines.extend(["", "🔬 <b>Advanced Analysis</b>", d])
        lines.extend(adv)

    # Social links from DexScreener
    socials = r.get("socials", {})
    if socials:
        soc_parts = []
        for stype, surl in socials.items():
            if stype == "twitter":
                soc_parts.append(f'<a href="{surl}">𝕏</a>')  # noqa: RUF001
            elif stype == "telegram":
                soc_parts.append(f'<a href="{surl}">📱</a>')
            elif stype == "website":
                soc_parts.append(f'<a href="{surl}">🌐</a>')
        if soc_parts:
            lines.extend(["", f"🔗 Socials: {' | '.join(soc_parts)}"])

    # Trade section
    lines.extend(["", s, "💱 <b>Trade:</b>"])

    # Footer
    lines.append(footer_links())

    return "\n".join(lines)


# ══════════════════════════════════════════════
# WALLET FORENSICS ENGINE
# ══════════════════════════════════════════════
async def analyze_wallet(address: str, chain: str | None = None) -> dict:
    result = {
        "address": address,
        "chain": chain or detect_chain(address),
        "balance_usd": 0,
        "tx_count": 0,
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": False,
        "is_contract": False,
        "risk_score": 0,
        "flags": [],
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{BACKEND_URL}/api/v1/databus/wallet/{address}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                for k in [
                    "balance_usd",
                    "tx_count",
                    "first_seen",
                    "is_exchange",
                    "is_contract",
                    "risk_score",
                    "flags",
                    "funding_source",
                    "pnl_estimate",
                ]:
                    if k in data:
                        result[k] = data[k]
                result["top_tokens"] = data.get("top_tokens", [])[:10]
                result["recent_txs"] = data.get("recent_txs", [])[:5]
                result["connected_wallets"] = data.get("connected_wallets", [])[:5]
                if result["first_seen"]:
                    try:
                        first = datetime.fromisoformat(result["first_seen"].replace("Z", "+00:00"))
                        result["wallet_age_days"] = (datetime.now(UTC) - first).days
                        result["is_fresh"] = result["wallet_age_days"] < 7
                    except Exception as e:
                        logger.warning(f"Failed to parse first_seen for {address}: {e}")
                return result
        except Exception as e:
            logger.warning(f"analyze_wallet RMI backend failed for {address}: {e}")
        if is_sol(address):
            try:
                r = await client.get(f"https://api.solana.fm/v0/accounts/{address}")
                if r.status_code == 200:
                    data = r.json()
                    sol_price = await _get_sol_price()
                    result["balance_usd"] = float(data.get("balance", 0)) / 1e9 * sol_price
                    result["tx_count"] = data.get("transaction_count", 0)
            except Exception as e:
                result["errors"].append(f"Solana.fm: {e}")
    return result


def _activity_score(tx_count: int | None) -> str:
    """Return activity-level label based on transaction count buckets."""
    if not tx_count or tx_count == 0:
        return "🟠 None"
    if tx_count < 10:
        return "🟡 Low"
    if tx_count < 100:
        return "🟢 Medium"
    if tx_count < 1000:
        return "🔵 High"
    return "💎 Very High"


def _explorer_link(address: str, chain: str) -> str:
    """Generate a block-explorer link for the given address."""
    if chain.lower() == "solana":
        return f'<a href="https://solscan.io/account/{address}">🔍 Solscan</a>'
    # EVM chains — Etherscan works for most; use generic link otherwise
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
    return f'<a href="https://{domain}/address/{address}">🔍 {domain.split(".")[0].title()}</a>'


def format_wallet_report(r: dict) -> str:
    d = thin_sep()
    s = sep()
    age_str = f"{r['wallet_age_days']} days" if r["wallet_age_days"] else "Unknown"
    tx_count = r.get("tx_count")
    tx_str = (
        f"📝 Transactions: <b>{tx_count:,}</b>"
        if tx_count is not None and tx_count > 0
        else "📝 Transactions: N/A"
    )
    score_str = _activity_score(tx_count)
    explorer_str = _explorer_link(r["address"], r["chain"])
    lines = [
        "👛 <b>Wallet Analysis</b>",
        s,
        f"<code>{r['address']}</code>",
        f"{fmt_chain(r['chain'])} | Age: {age_str} | {explorer_str}",
        "",
        "📊 <b>Overview</b>",
        d,
        f"💰 Balance: <b>{fmt_number(r['balance_usd'])}</b>",
        tx_str,
        f"📊 Activity Score: {score_str}",
        f"🏷️ Type: {'Exchange' if r['is_exchange'] else 'Contract' if r['is_contract'] else 'EOA (Wallet)'}",
    ]
    if r["is_fresh"]:
        lines.append("🆕 <b>FRESH WALLET</b> (<7 days) - ⚠️ Higher risk")
    if r["flags"]:
        lines.extend(["", "⚠️ <b>Flags</b>", d])
        for flag in r["flags"]:
            lines.append(f"  🔴 {flag}")
    if r["pnl_estimate"] is not None:
        pnl_e = "📈" if r["pnl_estimate"] >= 0 else "📉"
        lines.extend(["", f"{pnl_e} <b>Estimated P&L:</b> {fmt_number(r['pnl_estimate'])}"])
    if r["top_tokens"]:
        lines.extend(["", "🪙 <b>Top Holdings</b>", d])
        for i, t in enumerate(r["top_tokens"][:5], 1):
            lines.append(
                f"  {i}. <b>{t.get('symbol', '???')}</b> - {fmt_number(t.get('value_usd', 0))}"
            )
    if r["funding_source"]:
        lines.extend(["", f"🏦 <b>Funding Source:</b> {r['funding_source']}"])
    if r["connected_wallets"]:
        lines.extend(["", "🔗 <b>Connected Wallets</b>", d])
        for w in r["connected_wallets"][:5]:
            lines.append(f"  • <code>{short_addr(w)}</code>")
    if r["risk_score"] > 0:
        lines.extend(["", risk_bar(r["risk_score"])])
    lines.append(footer_links())
    return "\n".join(lines)


# ══════════════════════════════════════════════
# KEYBOARD BUILDERS
# ══════════════════════════════════════════════
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Scan Token", callback_data="menu_scan"),
                InlineKeyboardButton("👛 Wallet Check", callback_data="menu_wallet"),
            ],
            [
                InlineKeyboardButton("📊 Technical Analysis", callback_data="menu_ta"),
                InlineKeyboardButton("🔥 Trending", callback_data="menu_trending"),
            ],
            [
                InlineKeyboardButton("👀 Watchlist", callback_data="menu_watchlist"),
                InlineKeyboardButton("🔔 Alerts", callback_data="menu_alerts"),
            ],
            [
                InlineKeyboardButton("📋 My Account", callback_data="menu_account"),
                InlineKeyboardButton("⭐ Upgrade", callback_data="menu_pricing"),
            ],
            [
                InlineKeyboardButton("📚 Scam School", callback_data="menu_scamschool"),
                InlineKeyboardButton("🤝 Refer & Earn", callback_data="menu_refer"),
            ],
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("📢 Alerts Channel", url=TELEGRAM_CHANNEL),
            ],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("🔬 Deep Scan (250★)", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("⭐ Watch", callback_data=f"watch_{address}_{chain}"),
        ],
    ]
    dex_btns = dex_buttons(address, chain)
    for i in range(0, len(dex_btns), 2):
        rows.append(dex_btns[i : i + 2])
    rows.append([InlineKeyboardButton("◀️ Menu", callback_data="menu_main")])
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
    rows.append([InlineKeyboardButton("🌐 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")])
    rows.append([InlineKeyboardButton("📧 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    # Scan packs
    rows.append([InlineKeyboardButton("🔬 Scan Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "scans":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - ⭐{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    # AI packs
    rows.append([InlineKeyboardButton("🤖 AI Message Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "ai_msgs":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - ⭐{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    # Buy Stars
    rows.append([InlineKeyboardButton("⭐ Buy Stars", callback_data="menu_buystars")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def buy_stars_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in BUY_STARS_PACKS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - ⭐{pack['stars']}",
                    callback_data=f"buystars_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def scamschool_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, topic in SCAM_SCHOOL_TOPICS.items():
        rows.append([InlineKeyboardButton(topic["title"], callback_data=f"scam_{key}")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Main Menu", callback_data="menu_main")]])


def social_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 Website", url=WEBSITE_URL),
                InlineKeyboardButton("𝕏 Twitter", url=TWITTER_URL),  # noqa: RUF001
            ],
            [
                InlineKeyboardButton("📢 Alerts", url=TELEGRAM_CHANNEL),
                InlineKeyboardButton("💬 Chat", url=TELEGRAM_GROUP),
            ],
        ]
    )


# ══════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)

    # Referral handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if ref_code != u.get("referral_code") and not u.get("referred_by"):
            conn = db.get_db()
            referrer = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET referred_by = ?, bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (ref_code, user.id),
                )
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (referrer["user_id"],),
                )
                conn.commit()
            conn.close()

    proof = social_proof_text()
    text = (
        f"🛡️ <b>RugMunch Intelligence</b>\n"
        f"<i>The Bloomberg Terminal of Shitcoins</i>\n\n"
        f"{proof}\n\n"
        f"<b>What I can do for you:</b>\n\n"
        f"🔍 <b>Token Scanning</b>\n"
        f"  Honeypots, rug pulls, bundles, fake volume\n"
        f"  across 77+ chains in seconds\n\n"
        f"👛 <b>Wallet Forensics</b>\n"
        f"  Track smart money, dev wallets, funding sources\n\n"
        f"📊 <b>Market Intelligence</b>\n"
        f"  Technical analysis, trending tokens, alerts\n\n"
        f"🤖 <b>AI-Powered Risk Scoring</b>\n"
        f"  Advanced detection that goes beyond surface-level\n\n"
        f"<b>Quick Start:</b>\n"
        f"  📌 Paste any contract address → auto-scan\n"
        f"  📌 Type <code>$TOKEN</code> → quick price lookup\n"
        f"  📌 /scan <code>address</code> → full analysis\n"
        f"  📌 /help → see all commands\n\n"
        f"🆓 <b>Free:</b> {TIERS['free']['scans_per_week']} scans + {TIERS['free']['ai_msgs_per_week']} AI msgs/week\n"
        f"⭐ <b>Premium from $29.99/mo</b> - unlock everything"
        f"{footer_links()}"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Scan a Token", callback_data="menu_scan")],
            [
                InlineKeyboardButton("⭐ View Premium Plans", callback_data="menu_pricing"),
                InlineKeyboardButton("📚 Learn About Scams", callback_data="menu_scamschool"),
            ],
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("📢 Join Alerts", url=TELEGRAM_CHANNEL),
            ],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = thin_sep()
    text = (
        f"📖 <b>RugMunch Intelligence - Command Guide</b>\n"
        f"{sep()}\n\n"
        f"🔍 <b>TOKEN ANALYSIS</b>\n{d}\n"
        f"/scan <code>address</code> - Full security scan\n"
        f"/ta <code>address</code> - Technical analysis & signals\n"
        f"/chart <code>address</code> - Price chart & trends\n"
        f"/simulate <code>address</code> [amount] - Buy/sell simulation\n"
        f"/compare <code>addr1</code> <code>addr2</code> - Side-by-side\n"
        f"/quick <code>symbol</code> - Quick price check (same as $TOKEN)\n\n"
        f"👛 <b>WALLET INTELLIGENCE</b>\n{d}\n"
        f"/wallet <code>address</code> - Forensic wallet analysis\n"
        f"/portfolio <code>address</code> - Wallet portfolio scanner\n"
        f"/smartmoney <code>address</code> - Smart money detection\n"
        f"/rugmap <code>address</code> - Wallet cluster visualization\n"
        f"/watch <code>address</code> [chain] - Add to watchlist\n"
        f"/unwatch <code>address</code> - Remove from watchlist\n"
        f"/watchlist - View your watchlist\n"
        f"/alerts - Price & activity alerts\n\n"
        f"📊 <b>MARKET DATA</b>\n{d}\n"
        f"/trending - Hot tokens right now\n"
        f"/news - Latest crypto news\n\n"
        f"💳 <b>ACCOUNT & BILLING</b>\n{d}\n"
        f"/account - Dashboard & usage stats\n"
        f"/pricing - Subscription plans\n"
        f"/topup - Buy extra scans (no expiry)\n"
        f"/refer - Invite friends, earn scans\n\n"
        f"📚 <b>EDUCATION</b>\n{d}\n"
        f"/scamschool - Learn about crypto scams\n"
        f"/rugcheck - Quick rug pull checklist\n\n"
        f"💡 <b>PRO TIPS</b>\n{d}\n"
        f"• Paste any <code>0x...</code> address to auto-scan\n"
        f"• Type <code>$PEPE</code> for instant price check\n"
        f"• Use inline mode: <code>@RugMunchBot address</code> in any chat\n"
        f"• Upgrade for bundle detection, wallet forensics & more\n"
        f"• Use /refer to earn free scans"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL),
                InlineKeyboardButton("💬 Support", url=TELEGRAM_GROUP),
            ],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "🔍 <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "💡 <i>Or just paste a contract address directly!</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    msg = await update.message.reply_text(
        f"🔍 <b>Scanning token...</b>\n"
        f"<code>{short_addr(target)}</code>\n\n"
        f"⏳ Querying DexScreener, GoPlus, Honeypot.is, RMI...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                f"❌ <b>Token Not Found</b>\n\n"
                f"No data found for <code>{short_addr(target)}</code>.\n\n"
                f"• Check the address is correct\n"
                f"• Ensure the token has a trading pair\n"
                f"• Try specifying the chain: /scan <code>addr</code> solana",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        report = format_scan_report(result)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result["chain"]),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            f"❌ <b>Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "👛 <b>Wallet Forensics</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "<b>Example:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "<i>Hunter+ tiers get full P&L, funding source tracing, and connected wallet analysis.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(
        "👛 <b>Analyzing wallet...</b>\n⏳ Querying on-chain data...", parse_mode=ParseMode.HTML
    )
    try:
        result = await analyze_wallet(addr, chain)
        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            report, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            f"❌ <b>Wallet Analysis Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "📊 <b>Technical Analysis</b>\n\nUsage: /ta <code>address_or_symbol</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text(
        "📊 <b>Running Technical Analysis...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{target}")
            if r.status_code != 200:
                await msg.edit_text("❌ Token not found on DexScreener.", parse_mode=ParseMode.HTML)
                return
            pairs = r.json().get("pairs", [])
            if not pairs:
                await msg.edit_text("❌ No trading pairs found.", parse_mode=ParseMode.HTML)
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "???")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            price = float(p.get("priceUsd", 0))
            pc = p.get("priceChange", {})
            vol = p.get("volume", {})
            txns = p.get("txns", {})
            signals = []
            h1 = pc.get("h1") or 0
            h24 = pc.get("h24") or 0
            d7 = pc.get("d7") or 0
            if h1 > 5 and h24 > 10:
                signals.append("🟢 Strong short-term momentum")
            elif h1 < -5 and h24 < -10:
                signals.append("🔴 Strong downtrend")
            if h24 > 0 > h1:
                signals.append("🟡 Pullback in uptrend")
            if h24 < 0 < h1:
                signals.append("🟡 Bounce in downtrend")
            v24 = float(vol.get("h24") or 0)
            v6 = float(vol.get("h6") or 0)
            if v6 > 0 and v24 > 0:
                vol_trend = v6 / (v24 / 4)
                if vol_trend > 1.5:
                    signals.append("📈 Volume increasing (acceleration)")
                elif vol_trend < 0.5:
                    signals.append("📉 Volume declining (loss of interest)")
            buys = txns.get("h24", {}).get("buys", 0)
            sells = txns.get("h24", {}).get("sells", 0)
            total_tx = buys + sells
            if total_tx > 0:
                buy_ratio = buys / total_tx
                if buy_ratio > 0.7:
                    signals.append(f"🟢 Buy pressure dominant ({buy_ratio * 100:.0f}% buys)")
                elif buy_ratio < 0.3:
                    signals.append(
                        f"🔴 Sell pressure dominant ({(1 - buy_ratio) * 100:.0f}% sells)"
                    )
                else:
                    signals.append(f"⚪ Balanced buy/sell ({buy_ratio * 100:.0f}% buys)")
            text = (
                f"📊 <b>Technical Analysis</b>\n{sep()}\n"
                f"<b>{name}</b> ({symbol})\n<code>{target}</code>\n\n"
                f"💰 Price: <b>{fmt_number(price)}</b>\n"
                f"🔄 1h: {fmt_pct(h1)} | 24h: {fmt_pct(h24)} | 7d: {fmt_pct(d7)}\n\n"
                f"📈 <b>Volume</b>\n{thin_sep()}\n"
                f"6h: {fmt_number(v6)} | 24h: {fmt_number(v24)}\n"
                f"Txns 24h: {total_tx:,} (B:{buys:,} S:{sells:,})\n\n"
                f"🧭 <b>Signals</b>\n{thin_sep()}\n"
            )
            if signals:
                text += "\n".join(signals)
            else:
                text += "⚪ No strong signals detected"
            text += "\n\n<i>TA is not financial advice. Always /scan for security.</i>"
            text += footer_links()
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"TA error: {e}")
        await msg.edit_text(
            f"❌ TA failed: {str(e)[:200]}", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )


def _risk_indicator(score: float) -> str:
    """Return emoji indicator for a risk score."""
    if score >= 70:
        return "🔴"
    if score >= 40:
        return "🟡"
    return "🟢"


def _price_diff_pct(p1: float, p2: float) -> str:
    """Calculate price difference % between two tokens (relative to higher)."""
    if p1 <= 0 or p2 <= 0:
        return "N/A"
    diff = ((p1 - p2) / max(p1, p2)) * 100
    abs_diff = abs(diff)
    if abs_diff < 0.01:
        return "≈ same"
    cheaper = "T1" if diff < 0 else "T2"
    return f"{cheaper} {abs_diff:.2f}% cheaper"


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "⚖️ <b>Compare Tokens</b>\n\nUsage: /compare <code>addr1</code> <code>addr2</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return
    msg = await update.message.reply_text("⚖️ <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML)
    try:
        r1, r2 = await asyncio.gather(scan_token(ctx.args[0]), scan_token(ctx.args[1]))

        risk1 = r1["risk_score"]
        risk2 = r2["risk_score"]

        # Determine "better" token
        better = []
        if risk1 < risk2:
            better.append(f"✅ <b>{r1['name']}</b> — lower risk ({risk1}% vs {risk2}%)")
        elif risk2 < risk1:
            better.append(f"✅ <b>{r2['name']}</b> — lower risk ({risk2}% vs {risk1}%)")
        else:
            better.append(f"⚪ Both at {risk1}% risk")

        liq1 = r1["liquidity"] or 0
        liq2 = r2["liquidity"] or 0
        if liq1 > liq2 and liq1 > 0:
            better.append(f"💧 <b>{r1['name']}</b> — higher liquidity")
        elif liq2 > liq1 and liq2 > 0:
            better.append(f"💧 <b>{r2['name']}</b> — higher liquidity")

        price_diff = _price_diff_pct(r1["price"], r2["price"])

        def mini(r) -> str:
            risk_ind = _risk_indicator(r["risk_score"])
            return (
                f"<b>{r['name']}</b> ({r['symbol']})\n"
                f"  <code>{short_addr(r['address'])}</code>\n"
                f"  💰 Price: <b>{fmt_number(r['price'])}</b>\n"
                f"  📈 MCap: {fmt_number(r['mcap'])} | 💧 Liq: {fmt_number(r['liquidity'])}\n"
                f"  📊 Vol: {fmt_number(r['volume_24h'])} | 24h: {fmt_pct(r['price_change_24h'])}\n"
                f"  {risk_ind} Risk: <b>{r['risk_score']}%</b> | Threats: {len(r['threats'])}"
            )

        text = (
            f"⚖️ <b>Token Comparison</b>\n{sep()}\n\n"
            f"<b>1️⃣ Token A</b>\n{mini(r1)}\n\n"
            f"<b>2️⃣ Token B</b>\n{mini(r2)}\n\n"
            f"<b>📊 Price Difference:</b> {price_diff}\n\n"
            f"<b>🏆 Verdict</b>\n{thin_sep()}\n" + "\n".join(better) + footer_links()
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"cmd_compare failed: {e}")
        await msg.edit_text(
            "❌ <b>Comparison Failed</b>\n\nPlease check both addresses and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick price check - same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(
            "🪙 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE", parse_mode=ParseMode.HTML
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = float(p.get("volume", {}).get("h24") or 0)
                    mcap = float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    text = (
                        f"🪙 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"💰 {fmt_number(price)} | 🔄 24h: {fmt_pct(h24)}\n"
                        f"📈 MCap: {fmt_number(mcap)} | 💧 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔍 Full Scan", callback_data=f"scan_{addr}_{chain}"
                                )
                            ],
                            [web_scan_button(addr, chain)],
                        ]
                    )
                    await update.message.reply_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=True,
                    )
                    return
    except Exception as e:
        logger.warning(f"cmd_quick DexScreener lookup failed for symbol {symbol}: {e}")
    await update.message.reply_text(
        f"❌ Could not find token: <b>{symbol}</b>", parse_mode=ParseMode.HTML
    )


async def cmd_rugcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick rug pull checklist - educational."""
    text = (
        f"🚩 <b>Rug Pull Checklist</b>\n{sep()}\n\n"
        f"Before buying ANY token, check these:\n\n"
        f"<b>🔴 RED FLAGS (Run!)</b>\n{thin_sep()}\n"
        f"  ❌ Honeypot detected (can't sell)\n"
        f"  ❌ Unrenounced ownership\n"
        f"  ❌ Mintable supply (infinite tokens)\n"
        f"  ❌ Blacklist function exists\n"
        f"  ❌ LP not locked (< 50%)\n"
        f"  ❌ Buy/sell tax > 10%\n\n"
        f"<b>🟡 YELLOW FLAGS (Caution)</b>\n{thin_sep()}\n"
        f"  ⚠️ Bundle activity detected\n"
        f"  ⚠️ >50% fresh wallets (<7 days)\n"
        f"  ⚠️ Dev wallet holds >20%\n"
        f"  ⚠️ Suspicious volume/mcap ratio\n"
        f"  ⚠️ Very low liquidity vs mcap\n\n"
        f"<b>🟢 GREEN FLAGS (Safer)</b>\n{thin_sep()}\n"
        f"  ✅ Contract verified\n"
        f"  ✅ Ownership renounced\n"
        f"  ✅ LP locked > 90%\n"
        f"  ✅ No blacklist/mint functions\n"
        f"  ✅ Diverse holder distribution\n"
        f"  ✅ Organic volume patterns\n\n"
        f"<b>🛡️ Always run /scan before buying!</b>"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text(
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            boosts_r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
            if boosts_r.status_code != 200:
                await msg.edit_text("❌ Could not fetch trending data.", parse_mode=ParseMode.HTML)
                return
            boost_list = boosts_r.json()
            if not isinstance(boost_list, list) or not boost_list:
                await msg.edit_text("❌ No trending data available.", parse_mode=ParseMode.HTML)
                return
            boost_list = boost_list[:8]

            # Enrich with DexScreener token data for price/volume
            enriched = {}
            addresses = {b.get("tokenAddress", "") for b in boost_list if b.get("tokenAddress")}
            for addr in addresses:
                try:
                    tr = await client.get(f"{DEXSCREENER}/tokens/{addr}", timeout=5)
                    if tr.status_code == 200:
                        pairs = tr.json().get("pairs", [])
                        if pairs:
                            enriched[addr] = pairs[0]
                except Exception as e:
                    logger.debug(f"Trending enrichment failed for {addr}: {e}")

            lines = ["🔥 <b>Trending Tokens</b>", sep(), thin_sep()]
            for i, b in enumerate(boost_list, 1):
                addr = b.get("tokenAddress", "")
                name = b.get("name") or b.get("description", "???")[:15]
                symbol = b.get("symbol", "???")
                chain = b.get("chainId", "?")
                boost_count = b.get("boostCount", 0)
                boost_str = f" 🔥{boost_count}" if boost_count else ""

                pair = enriched.get(addr)
                if pair:
                    price = float(pair.get("priceUsd", 0))
                    h24 = pair.get("priceChange", {}).get("h24") or 0
                    vol = float(pair.get("volume", {}).get("h24") or 0)
                    emoji = "🟢" if h24 >= 0 else "🔴"
                    lines.append(
                        f"{i}. {emoji} <b>{name}</b> ({symbol}) {chain}\n"
                        f"   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}{boost_str}\n"
                        f"   <code>{short_addr(addr)}</code>"
                    )
                else:
                    lines.append(
                        f"{i}. 🔥 <b>{name}</b> ({symbol}) {chain}{boost_str}\n"
                        f"   <code>{short_addr(addr)}</code>"
                    )

            lines.append(footer_links())
            trending_kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="trending_refresh")],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=trending_kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        logger.error(f"cmd_trending failed: {e}")
        await msg.edit_text(
            "❌ <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )


def _time_ago(dt_iso: str | None) -> str:
    """Convert ISO timestamp to human readable relative time."""
    if not dt_iso:
        return ""
    try:
        dt_iso_clean = dt_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_iso_clean)
        diff = datetime.now(UTC) - dt
        total_secs = diff.total_seconds()
        if total_secs < 0:
            return "just now"
        if total_secs < 60:
            return f"{int(total_secs)}s ago"
        if total_secs < 3600:
            return f"{int(total_secs / 60)}m ago"
        if total_secs < 86400:
            return f"{int(total_secs / 3600)}h ago"
        return f"{diff.days}d ago"
    except Exception:
        return ""


async def _fetch_cg_trending_news() -> list[dict] | None:
    """Fallback: fetch CoinGecko trending coins as pseudo-news items."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if r.status_code != 200:
                return None
            coins = r.json().get("coins", [])
            if not coins:
                return None
            items = []
            for c in coins[:5]:
                item = c.get("item", {})
                items.append(
                    {
                        "title": f"{item.get('name', 'Unknown')} ({item.get('symbol', '?')}) "
                        f"trending on CoinGecko",
                        "source": "CoinGecko",
                        "published_at": None,
                        "url": f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
                        "score": item.get("score", 0) or 0,
                    }
                )
            return items
    except Exception as exc:
        logger.debug("CoinGecko trending fallback failed: %s", exc)
        return None


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("📰 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)
    articles = []
    source_label = "Backend"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5")
            if r.status_code == 200:
                body = r.json()
                articles = body.get("items", [])
    except Exception as e:
        logger.warning("cmd_news backend fetch failed: %s", e)

    # Fallback: CoinGecko trending if backend fails
    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "CoinGecko"

    news_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="news_refresh")],
            [InlineKeyboardButton("📢 Alerts Channel", url=TELEGRAM_CHANNEL)],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )

    if articles:
        lines = ["📰 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
        for a in articles[:5]:
            title = (a.get("title") or "Untitled")[:100]
            source = a.get("source", "News")
            published = _time_ago(a.get("published_at") or a.get("publishedAt"))
            pub_str = f" · {published}" if published else ""
            url = a.get("url") or a.get("link") or ""
            title_linked = f'<a href="{url}">{title}</a>' if url else title
            lines.append(f"📰 {title_linked}\n  <i>{source}</i>{pub_str}")
        lines.append(footer_links())
        text = "\n".join(lines)
    else:
        text = (
            f"📰 <b>Crypto News</b>\n\n"
            f"<i>News feed temporarily unavailable.</i>\n\n"
            f'📢 Follow <a href="{TELEGRAM_CHANNEL}">@RugMunchAlerts</a> for real-time alerts!\n'
            f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a>'
        )

    with contextlib.suppress(Exception):
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=news_kb,
            disable_web_page_preview=True,
        )


async def cmd_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    tier = db.get_user_tier(user.id)
    tc = TIERS.get(tier, TIERS["free"])
    scans, ai = db.get_weekly_usage(user.id)
    stats = db.get_user_stats(user.id)
    tier_badge = f"{tc['emoji']} {tc['name']}"
    if u.get("tier_expires") and u["tier_expires"] > 0:
        exp = datetime.fromtimestamp(u["tier_expires"], tz=UTC).strftime("%b %d, %Y")
        tier_badge += f" (expires {exp})"

    # Premium nudge for free users
    nudge = ""
    if tier == "free":
        nudge = (
            f"\n{thin_sep()}\n"
            f"⭐ <b>Upgrade to unlock:</b>\n"
            f"  • Bundle detection & fake volume alerts\n"
            f"  • Wallet forensics & smart money tracking\n"
            f"  • Real-time price alerts & watchlist\n"
            f"  • Up to 1,000 scans/week\n"
        )

    text = (
        f"👤 <b>Account Dashboard</b>\n{sep()}\n"
        f"<b>User:</b> @{user.username or user.first_name}\n"
        f"<b>Tier:</b> {tier_badge}\n\n"
        f"📊 <b>This Week</b>\n{thin_sep()}\n"
        f"🔍 Scans: <b>{scans}/{tc.get('scans_per_week', 25)}</b>\n"
        f"🤖 AI Messages: <b>{ai}/{tc.get('ai_msgs_per_week', 15)}</b>\n\n"
        f"🎁 <b>Bonus Balance</b>\n{thin_sep()}\n"
        f"🔬 Extra Scans: {u.get('bonus_scans', 0)}\n"
        f"🤖 Extra AI Msgs: {u.get('bonus_ai_msgs', 0)}\n"
        f"⭐ Stars: {u.get('stars_balance', 0)}\n\n"
        f"📈 <b>All-Time</b>\n{thin_sep()}\n"
        f"Total Scans: {u.get('total_scans', 0)}\n"
        f"Watchlist: {stats.get('watchlist_count', 0)} tokens\n"
        f"{nudge}"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⭐ Upgrade Plan", callback_data="menu_pricing"),
                InlineKeyboardButton("🔋 Top Up", callback_data="menu_topup"),
            ],
            [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
        ]
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
    )


async def cmd_pricing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    balance = 0
    if user:
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        balance = u.get("stars_balance", 0)
    d = thin_sep()
    text = f"⭐ <b>Subscription Plans</b>\n{sep()}\n\n"
    text += f"Your balance: ⭐{balance:,}\n\n"
    for key, t in TIERS.items():
        if key == "free":
            text += f"{t['emoji']} <b>{t['name']}</b> - $0\n{d}\n"
        else:
            text += f"{t['emoji']} <b>{t['name']}</b> - <b>${t['price_monthly']}/mo</b>\n{d}\n"
        for f in t["features"]:
            text += f"  • {f}\n"
        text += "\n"
    text += (
        f"{sep()}\n"
        f"💳 Pay with Telegram Stars\n"
        f"🔄 Weekly reset Monday 00:00 UTC\n\n"
        f'🌐 <a href="{WEBSITE_URL}/pricing">Compare plans on rugmunch.io</a>\n'
        f"📧 Enterprise: {SUPPORT_EMAIL}"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=pricing_kb(), disable_web_page_preview=True
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    balance = 0
    if user:
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        balance = u.get("stars_balance", 0)
    text = (
        f"🔋 <b>Top Up - Buy Extra Usage</b>\n{sep()}\n\n"
        f"Your balance: ⭐{balance:,}\n\n"
        f"Ran out of weekly scans? Buy bonus usage!\n\n"
        f"✅ <b>No expiry</b> - carries over forever\n"
        f"✅ <b>Stackable</b> - buy multiple times\n"
        f"✅ <b>Instant</b> - available immediately\n\n"
        f"💳 Pay with Telegram Stars ⭐"
        f"{footer_links()}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=topup_kb())


async def cmd_buystars(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """⭐ Buy Telegram Stars - purchase Stars to spend on scans, subs, etc."""
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    text = (
        f"⭐ <b>Buy Telegram Stars</b>\n{sep()}\n\n"
        f"Current balance: ⭐{u.get('stars_balance', 0):,}\n\n"
        f"Purchase Stars via Telegram and spend them on scans, "
        f"subscriptions, and premium features!\n\n"
        f"Select a pack below:"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buy_stars_kb())


async def cmd_scamschool(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        f"📚 <b>Scam School</b>\n{sep()}\n\n"
        f"Learn how to spot crypto scams <b>before</b> you get rugged.\n\n"
        f'Free education from <a href="{WEBSITE_URL}">RugMunch Intelligence</a>.\n\n'
        f"Select a topic below 👇"
        f"{footer_links()}"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=scamschool_kb(), disable_web_page_preview=True
    )


async def cmd_refer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    ref = u.get("referral_code", "UNKNOWN")
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{ref}"
    conn = db.get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (ref,)
    ).fetchone()["c"]
    conn.close()
    milestones = {5: "🥉", 10: "🥈", 25: "🥇", 50: "💎", 100: "👑"}
    next_ms = next((k for k in sorted(milestones.keys()) if k > count), None)
    ms_text = (
        f"\n🎯 Next milestone: {milestones[next_ms]} {next_ms} referrals"
        if next_ms
        else "\n👑 You've hit all milestones!"
    )
    text = (
        f"🤝 <b>Refer & Earn</b>\n{sep()}\n\n"
        f"Invite friends and <b>both get 5 bonus scans!</b>\n\n"
        f"🔗 <b>Your Link:</b>\n<code>{link}</code>\n\n"
        f"📊 <b>Your Stats:</b>\n"
        f"  Friends Referred: <b>{count}</b>\n"
        f"  Bonus Earned: <b>{count * 5}</b> scans{ms_text}\n\n"
        f"<b>How it works:</b>\n"
        f"  1. Share your referral link\n"
        f"  2. Friend starts the bot via your link\n"
        f"  3. Both of you get 5 bonus scans instantly\n"
        f"  4. No limits - refer as many as you want!"
        f"{footer_links()}"
    )
    share_text = quote(
        f"Check out RugMunch Intelligence - the best crypto scam detector on Telegram! 🛡️\n\n{link}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📤 Share Link", url=f"https://t.me/share/url?url={link}&text={share_text}"
                    )
                ],
                [InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        ),
        disable_web_page_preview=True,
    )


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    items = db.get_watchlist(user.id)
    if not items:
        await update.message.reply_text(
            "👀 <b>Watchlist Empty</b>\n\n"
            "Add tokens to track:\n"
            "  /watch <code>address</code> [chain]\n\n"
            "<i>Scout: 10 slots | Hunter: 50 | Pro: unlimited</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    tier = db.get_user_tier(user.id)
    text = f"👀 <b>Watchlist</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"
    for i, w in enumerate(items, 1):
        alert = ""
        if w.get("alert_price"):
            arrow = "↑" if w.get("alert_direction") == "above" else "↓"
            alert = f" 🔔{arrow}{fmt_number(w['alert_price'])}"
        text += f"{i}. <b>{w.get('symbol') or '???'}</b> <code>{short_addr(w['token_address'])}</code> ({w['chain']}){alert}\n"
    text += f"\n{footer_links()}"
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "👀 Usage: /watch <code>address</code> [chain]", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else detect_chain(addr)
    symbol = ctx.args[2] if len(ctx.args) > 2 else None
    ok = db.add_watchlist(user.id, addr, chain, symbol)
    if ok:
        await update.message.reply_text(
            f"✅ Added <code>{short_addr(addr)}</code> to watchlist.\n/watchlist to view all.",
            parse_mode=ParseMode.HTML,
        )
    else:
        tier = db.get_user_tier(user.id)
        if tier == "free":
            await update.message.reply_text(
                "❌ Watchlists require <b>Scout tier</b> or higher.\n\n"
                "🔍 <b>Scout ($29.99/mo)</b> - 10 watchlist slots\n"
                "🎯 <b>Hunter ($49.99/mo)</b> - 50 slots + alerts\n"
                "👑 <b>Pro ($99.99/mo)</b> - unlimited",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
        else:
            await update.message.reply_text(
                "❌ Could not add. Limit reached or already added.", parse_mode=ParseMode.HTML
            )


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /unwatch <code>address</code>", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    ok = db.remove_watchlist(user.id, addr)
    if ok:
        await update.message.reply_text(
            f"✅ Removed <code>{short_addr(addr)}</code> from watchlist.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "❌ Token not found in your watchlist.", parse_mode=ParseMode.HTML
        )


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(
            f"🔔 <b>Price Alerts</b>\n\n"
            f"Get notified when tokens hit your target price.\n\n"
            f"⚠️ <b>Requires Scout tier</b> or higher.\n"
            f"You're on {TIERS[tier]['emoji']} {TIERS[tier]['name']}.\n\n"
            f"🎯 <b>Hunter+</b> gets real-time exchange flow alerts too!",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    # Fetch all watchlist entries (including triggered ones) for alert info
    items = db.get_watchlist_for_user(user.id, include_triggered=True)
    active_alerts = [w for w in items if w.get("alert_price") and w.get("is_active")]
    triggered_alerts = [
        w
        for w in items
        if w.get("alert_price") and not w.get("is_active") and w.get("alert_triggered_at")
    ]

    if not active_alerts and not triggered_alerts:
        await update.message.reply_text(
            "🔔 <b>Price Alerts</b>\n\n"
            "No alerts set yet.\n\n"
            "To set an alert:\n"
            "  /alert ABOVE <code>address</code> 0.005\n"
            "  /alert BELOW <code>address</code> 0.85\n"
            "  /alert CHANGE_5 <code>address</code>\n"
            "  /alert ANY <code>address</code> 0.01",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    text = f"🔔 <b>Alerts</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"

    if active_alerts:
        text += "<b>🟢 Active</b>\n"
        for a in active_alerts:
            arrow = "↑" if a.get("alert_direction") == "above" else "↓"
            text += f"  • <b>{a.get('symbol', '???')}</b> {arrow} {fmt_number(a['alert_price'])}\n"
        text += "\n"

    if triggered_alerts:
        text += "<b>🔴 Triggered (recent)</b>\n"
        for a in triggered_alerts[:5]:  # Show last 5
            ts_str = ""
            if a.get("alert_triggered_at"):
                try:
                    ts = a["alert_triggered_at"]
                    if "T" in ts:
                        ts_str = f" — {ts[:10]}"
                except Exception:
                    pass
            text += (
                f"  • <b>{a.get('symbol', '???')}</b> 🔔 {fmt_number(a['alert_price'])}{ts_str}\n"
            )
        if len(triggered_alerts) > 5:
            text += f"  ... and {len(triggered_alerts) - 5} more\n"

    text += "\nSet new: /alert ABOVE <code>addr</code> <price>"
    text += footer_links()
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


# ══════════════════════════════════════════════
# OWNER COMMANDS
# ══════════════════════════════════════════════
async def cmd_admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    tiers = conn.execute("SELECT tier, COUNT(*) as c FROM users GROUP BY tier").fetchall()
    scans_today = (
        conn.execute(
            "SELECT SUM(scans) as c FROM weekly_usage WHERE week_start = ?",
            (db._current_week_start(),),
        ).fetchone()["c"]
        or 0
    )
    total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    tier_str = "\n".join(f"  {t['tier']}: {t['c']}" for t in tiers)
    await update.message.reply_text(
        f"📊 <b>Bot Stats</b>\n{thin_sep()}\n"
        f"👥 Total Users: {total}\n🚫 Banned: {banned}\n\n"
        f"📈 <b>Tiers:</b>\n{tier_str}\n\n"
        f"🔍 This Week: {scans_today}\n🔍 All-Time: {total_scans}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_admin_set_tier(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")
        return
    uid, tier = int(ctx.args[0]), ctx.args[1].lower()
    days = int(ctx.args[2]) if len(ctx.args) > 2 else 30
    if tier not in TIERS:
        await update.message.reply_text(f"Invalid tier. Options: {', '.join(TIERS.keys())}")
        return
    db.set_user_tier(uid, tier, days)
    await update.message.reply_text(f"✅ Set user {uid} to {tier} ({days} days)")


async def cmd_admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")
        return
    msg_text = " ".join(ctx.args).replace("\\n", "\n")
    conn = db.get_db()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.debug(f"Broadcast failed for user {u['user_id']}: {e}")
            failed += 1
    await update.message.reply_text(f"📢 Broadcast complete.\n✅ Sent: {sent}\n❌ Failed: {failed}")


async def cmd_admin_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_ban <user_id> [0|1]")
        return
    uid, state = int(ctx.args[0]), int(ctx.args[1]) if len(ctx.args) > 1 else 1
    conn = db.get_db()
    conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (state, uid))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"{'🚫' if state else '✅'} User {uid} {'banned' if state else 'unbanned'}."
    )


# ══════════════════════════════════════════════
# MESSAGE HANDLER (Auto-detect + AI Chat)
# ══════════════════════════════════════════════
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    if check_spam(user.id):
        return

    # Auto-detect contract address
    if is_evm(text) or is_sol(text):
        ctx.args = [text]
        await cmd_scan(update, ctx)
        return

    # Token symbol detection ($TOKEN)
    token_match = TOKEN_RE.search(text)
    if token_match:
        symbol = token_match.group(1).upper()
        await update.message.chat.send_action("typing")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        addr = p.get("baseToken", {}).get("address", "")
                        chain = p.get("chainId", "")
                        price = float(p.get("priceUsd", 0))
                        h24 = p.get("priceChange", {}).get("h24") or 0
                        vol = float(p.get("volume", {}).get("h24") or 0)
                        mcap = float(p.get("marketCap") or 0)
                        name = p.get("baseToken", {}).get("name", symbol)
                        quick_text = (
                            f"🪙 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                            f"<code>{addr}</code>\n\n"
                            f"💰 {fmt_number(price)} | 🔄 24h: {fmt_pct(h24)}\n"
                            f"📈 MCap: {fmt_number(mcap)} | 💧 Vol: {fmt_number(vol)}"
                            f"{footer_links()}"
                        )
                        if addr:
                            await update.message.reply_text(
                                quick_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=InlineKeyboardMarkup(
                                    [
                                        [
                                            InlineKeyboardButton(
                                                "🔍 Full Scan", callback_data=f"scan_{addr}_{chain}"
                                            )
                                        ],
                                        [web_scan_button(addr, chain)],
                                    ]
                                ),
                                disable_web_page_preview=True,
                            )
                            return
        except Exception as e:
            logger.debug(f"handle_message $TOKEN lookup failed for {symbol}: {e}")

    # Natural language address extraction
    evm_match = EVM_RE.search(text)
    sol_match = SOL_RE.search(text)
    if evm_match:
        ctx.args = [evm_match.group()]
        await cmd_scan(update, ctx)
        return
    if sol_match:
        ctx.args = [sol_match.group()]
        await cmd_scan(update, ctx)
        return

    # AI Chat — Tiered fallback (RAG → local intent → friendly redirect)
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        await update.message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    await update.message.chat.send_action("typing")

    # Tier 1: RMI RAG backend
    rag_response = await _try_rag_backend(text)
    if rag_response:
        logger.info("AI chat tier=1 (rag) user=%s text=%.60s", user.id, text)
        db.increment_usage(user.id, ai_msg=True)
        await update.message.reply_text(
            rag_response[:4000], parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return

    # Tier 2: Local intent matching
    local_response = local_ai_response(text, user)
    if local_response:
        logger.info("AI chat tier=2 (local intent) user=%s text=%.60s", user.id, text)
        db.increment_usage(user.id, ai_msg=True)
        await update.message.reply_text(
            local_response,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
        return

    # Tier 3: Friendly redirect (catch-all)
    logger.info("AI chat tier=3 (redirect) user=%s text=%.60s", user.id, text)
    db.increment_usage(user.id, ai_msg=True)
    await update.message.reply_text(
        "🤖 <b>RugMunch AI</b>\n\n"
        "I can help with token scans, wallet analysis, and market data. "
        "Use <code>/scan &lt;address&gt;</code> or just paste a token address!\n\n"
        "💡 <b>Quick tips:</b>\n"
        "• <code>$TOKEN</code> → instant price lookup\n"
        "• <code>/wallet &lt;addr&gt;</code> → wallet analysis\n"
        "• <code>/trending</code> → hot tokens\n"
        "• <code>/scamschool</code> → learn scam patterns"
        f"{footer_links()}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════
# RAG BACKEND HELPER
# ══════════════════════════════════════════════
async def _try_rag_backend(query: str) -> str | None:
    """Tier 1: Query RMI RAG backend. Returns response text or None."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BACKEND_URL}/api/v1/rag/v2/search",
                json={
                    "query": query,
                    "collection": "scam_intel",
                    "top_k": 5,
                    "min_similarity": 0.0,
                    "filters": {},
                },
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", [])
                if hits:
                    answer = hits[0].get("content", "")
                    if answer:
                        return answer
    except httpx.ConnectError:
        logger.debug("RAG backend unreachable (connection refused)")
    except httpx.TimeoutException:
        logger.debug("RAG backend timeout")
    except Exception as e:
        logger.debug("RAG backend error: %s", e)
    return None


# ══════════════════════════════════════════════
# LOCAL INTENT MATCHING (Tier 2)
# ══════════════════════════════════════════════
_INTENT_RULES: list[tuple[re.Pattern, str]] = [
    # ── Scan / Security ──
    (
        re.compile(
            r"\b(scan|check|audit|rug|scam|honeypot|safu|drain|phish|malicious|"
            r"dangerous|secure|risky|safe(ty)?|is this safe|is it safe|trustworthy)\b",
            re.IGNORECASE,
        ),
        (
            "🔍 <b>Token Security Scan</b>\n\n"
            "Use <code>/scan &lt;address&gt;</code> to run a full security audit. "
            "I check for:\n"
            "• 🍯 Honeypot detection\n"
            "• 🏃 Rug pull risks\n"
            "• 📦 Bundle / wash trading\n"
            "• 👛 Dev wallet history\n"
            "• 📊 Liquidity & holder analysis\n\n"
            "Just paste any contract address and I'll auto-detect it!"
        ),
    ),
    # ── Price / Chart / Market ──
    (
        re.compile(
            r"\b(price|chart|worth|value|how much|cost|mc[aap]+|market cap|volume|"
            r"liquidity|pump|dump|buy|sell|token price|usd)\b",
            re.IGNORECASE,
        ),
        (
            "📊 <b>Price & Market Data</b>\n\n"
            "Use <code>/quick &lt;symbol&gt;</code> or <code>$TOKEN</code> for instant prices.\n"
            "Use <code>/chart &lt;address&gt;</code> for technical analysis charts.\n"
            "Use <code>/scan &lt;address&gt;</code> for full market data.\n\n"
            "💰 I pull real-time data from DexScreener and CoinGecko."
        ),
    ),
    # ── Wallet / Portfolio ──
    (
        re.compile(
            r"\b(wallet|portfolio|balance|holdings|my token|my coin|bag|"
            r"hodl|fund|money)\b",
            re.IGNORECASE,
        ),
        (
            "👛 <b>Wallet Analysis</b>\n\n"
            "Use <code>/wallet &lt;address&gt;</code> to analyze any wallet.\n"
            "Use <code>/portfolio</code> to view your tracked wallets.\n\n"
            "I show:\n"
            "• 💰 Token balances & values\n"
            "• 🔄 Recent transaction history\n"
            "• ⚠️ Risk metrics & scam checks\n"
            "• 🔗 Counterparty connections"
        ),
    ),
    # ── Compare ──
    (
        re.compile(
            r"\b(compare|vs |versus|better than|which (is|one)|difference)\b", re.IGNORECASE
        ),
        (
            "⚖️ <b>Token Comparison</b>\n\n"
            "Use <code>/compare &lt;addr1&gt; &lt;addr2&gt;</code> to pit two tokens "
            "side-by-side.\n\n"
            "I'll show you both risk scores, prices, liquidity, and holder metrics "
            "in one view!"
        ),
    ),
    # ── Trending / Hot / Popular ──
    (
        re.compile(
            r"\b(trending|hot|popular|what to buy|best coin|moon|gem|"
            r"next 100x|top token|gainers|movers)\b",
            re.IGNORECASE,
        ),
        (
            "🔥 <b>Trending Tokens</b>\n\n"
            "Check <code>/trending</code> for the hottest tokens right now.\n"
            "I show real-time data on:\n"
            "• 📈 Top gainers\n"
            "• 💧 Highest volume\n"
            "• 🆕 New pairs\n"
            "• 🏠 Most holders"
        ),
    ),
    # ── Alerts / Watch / Notify ──
    (
        re.compile(
            r"\b(alert|notify|watch|track|monitor|notification|remind|follow)\b",
            re.IGNORECASE,
        ),
        (
            "🔔 <b>Price Alerts</b>\n\n"
            "Use <code>/watch &lt;address&gt;</code> to add tokens to your watchlist.\n"
            "Use <code>/alerts</code> to manage your price alerts.\n\n"
            "📌 <b>Scout+</b> tier gets real-time push alerts for price moves, "
            "large transactions, and security events."
        ),
    ),
    # ── Greetings ──
    (
        re.compile(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|"
            r"gm|gn|what'?s up|welcome)\b",
            re.IGNORECASE,
        ),
        (
            "👋 <b>Hey there!</b>\n\n"
            "I'm <b>RugMunch</b> — your crypto security sidekick. "
            "I scan tokens for scams, analyze wallets, track prices, and more across 77+ chains.\n\n"
            "🛡️ <b>Try these:</b>\n"
            "• Paste a contract address → auto scan\n"
            "• <code>$TOKEN</code> → instant price\n"
            "• <code>/help</code> → all commands"
            f"{footer_links()}"
        ),
    ),
    # ── Thanks / Acknowledgment ──
    (
        re.compile(r"\b(thank|thanks|ty|thx|appreciate|good bot|nice|great)\b", re.IGNORECASE),
        (
            "🙌 You're welcome! Happy to help keep the crypto space safe.\n\n"
            "If you find this useful, tell your friends! \n"
            "Use <code>/refer</code> to get your referral link."
        ),
    ),
    # ── Help / Capabilities (catch-all after more specific patterns) ──
    (
        re.compile(
            r"\b(help|how|what|can you|capabilities|features|commands|what do|"
            r"what are|guide|tutorial|docs|documentation)\b",
            re.IGNORECASE,
        ),
        (
            "🤖 <b>RugMunch Capabilities</b>\n\n"
            "I'm a multi-chain crypto intelligence bot. Here's what I can do:\n\n"
            "🔍 <code>/scan &lt;addr&gt;</code> — Full token security audit\n"
            "👛 <code>/wallet &lt;addr&gt;</code> — Wallet forensics\n"
            "📊 <code>/quick &lt;symbol&gt;</code> — Price lookup\n"
            "📈 <code>/chart &lt;addr&gt;</code> — Technical analysis\n"
            "⚖️ <code>/compare &lt;a&gt; &lt;b&gt;</code> — Side-by-side compare\n"
            "🔥 <code>/trending</code> — Hot tokens\n"
            "📰 <code>/news</code> — Crypto headlines\n"
            "🎓 <code>/scamschool</code> — Learn scam patterns\n"
            "👑 <code>/pricing</code> — Upgrade your tier"
            f"{footer_links()}"
        ),
    ),
]


def local_ai_response(text: str, user: Any) -> str | None:
    """Tier 2: Match user intent against keyword patterns.

    Returns a helpful response string or None if no pattern matched.
    Tier 3 (catch-all redirect) handles the None case.
    """
    text_lower = text.lower().strip()

    for pattern, response in _INTENT_RULES:
        if pattern.search(text_lower):
            # Don't match single-word "hi" in Korean/other languages
            # or very short noise — let Tier 3 handle ultra-short messages
            if len(text_lower) < 2:
                return None
            return response

    return None


# ══════════════════════════════════════════════
# INLINE QUERY (Scan from any chat)
# ══════════════════════════════════════════════
async def handle_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query.strip()
    if not query:
        return
    results = []
    if is_evm(query) or is_sol(query):
        results.append(
            InlineQueryResultArticle(
                id="scan_" + query[:10],
                title=f"🔍 Scan {short_addr(query)}",
                description="Run a full security scan on this address",
                input_message_content=InputTextMessageContent(f"/scan {query}"),
            )
        )
    if len(query) >= 2 and len(query) <= 10 and query.isalpha():
        results.append(
            InlineQueryResultArticle(
                id="sym_" + query,
                title=f"🪙 Lookup ${query.upper()}",
                description="Find token price and info",
                input_message_content=InputTextMessageContent(f"${query.upper()}"),
            )
        )
    if results:
        await update.inline_query.answer(results, cache_time=30, is_personal=True)


# ══════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════
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
            "🛡️ <b>RugMunch Intelligence</b>\n\nWhat would you like to do?",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    elif data == "menu_scan":
        await query.edit_message_text(
            "🔍 <b>Scan a Token</b>\n\nSend me a contract address:\n<code>0x6982...1933</code>\n\nOr use: /scan <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_wallet":
        await query.edit_message_text(
            "👛 <b>Wallet Check</b>\n\nSend a wallet address or use:\n/wallet <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_ta":
        await query.edit_message_text(
            "📊 <b>Technical Analysis</b>\n\nUsage: /ta <code>address</code>\n\nAnalyzes momentum, volume, buy/sell pressure.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_alerts":
        await query.edit_message_text(
            "🔔 <b>Price Alerts</b>\n\nUse /alerts to manage.\n\n<i>Scout+ tiers only.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_account":
        tier = db.get_user_tier(user.id)
        tc = TIERS.get(tier, TIERS["free"])
        scans, ai = db.get_weekly_usage(user.id)
        u = db.get_user_stats(user.id)
        text = (
            f"👤 <b>Account</b>\n{thin_sep()}\n"
            f"Tier: {tc['emoji']} {tc['name']}\n"
            f"Scans: {scans}/{tc.get('scans_per_week', 25)}\n"
            f"AI: {ai}/{tc.get('ai_msgs_per_week', 15)}\n"
            f"Bonus Scans: {u.get('bonus_scans', 0)}\n"
            f"Bonus AI: {u.get('bonus_ai_msgs', 0)}\n"
            f"Stars: {u.get('stars_balance', 0)}⭐\n"
            f"Watchlist: {u.get('watchlist_count', 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⭐ Upgrade", callback_data="menu_pricing"),
                        InlineKeyboardButton("🔋 Top Up", callback_data="menu_topup"),
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            ),
        )
    elif data == "menu_pricing":
        await query.edit_message_text(
            "⭐ <b>Upgrade Plan</b>\n\nSelect a tier:",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
    elif data == "menu_topup":
        await query.edit_message_text(
            "🔋 <b>Top Up</b>\n\nBuy extra usage (no expiry):",
            parse_mode=ParseMode.HTML,
            reply_markup=topup_kb(),
        )
    elif data == "menu_scamschool":
        await query.edit_message_text(
            "📚 <b>Scam School</b>\n\nSelect a topic:",
            parse_mode=ParseMode.HTML,
            reply_markup=scamschool_kb(),
        )
    elif data == "menu_trending":
        await query.edit_message_text("🔥 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
                if r.status_code == 200:
                    boost_list = r.json()
                    if not isinstance(boost_list, list):
                        boost_list = []
                    lines = ["🔥 <b>Trending</b>", thin_sep()]
                    for b in boost_list[:6]:
                        name = (b.get("name") or b.get("description", "???"))[:12]
                        sym = b.get("symbol", "???")
                        addr = b.get("tokenAddress", "")
                        boost_count = b.get("boostCount", 0)
                        boost_str = f" 🔥{boost_count}" if boost_count else ""
                        lines.append(
                            f"🔥 <b>{name}</b> ({sym}){boost_str}\n   <code>{short_addr(addr)}</code>"
                        )
                    await query.edit_message_text(
                        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_kb()
                    )
                else:
                    await query.edit_message_text(
                        "❌ Could not fetch trending data.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                    )
        except Exception as e:
            logger.warning(f"handle_callback trending failed: {e}")
            await query.edit_message_text(
                "❌ Trending unavailable. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data == "trending_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("🔄 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                boosts_r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
                if boosts_r.status_code == 200:
                    boost_list = boosts_r.json()
                    if isinstance(boost_list, list):
                        boost_list = boost_list[:6]
                        # Quick enrich with DexScreener price
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
                            except Exception:
                                pass
                        lines = ["🔥 <b>Trending Tokens</b>", sep()]
                        for i, b in enumerate(boost_list, 1):
                            addr = b.get("tokenAddress", "")
                            name = (b.get("name") or b.get("description", "???"))[:12]
                            sym = b.get("symbol", "???")
                            chain = b.get("chainId", "?")
                            bc = b.get("boostCount", 0)
                            bs = f" 🔥{bc}" if bc else ""
                            pair = enriched.get(addr)
                            if pair:
                                price = float(pair.get("priceUsd", 0))
                                h24 = pair.get("priceChange", {}).get("h24") or 0
                                vol = float(pair.get("volume", {}).get("h24") or 0)
                                emoji = "🟢" if h24 >= 0 else "🔴"
                                lines.append(
                                    f"{i}. {emoji} <b>{name}</b> ({sym}) {chain}\n"
                                    f"   {fmt_number(price)} | {fmt_pct(h24)} | Vol: {fmt_number(vol)}{bs}"
                                )
                            else:
                                lines.append(f"{i}. 🔥 <b>{name}</b> ({sym}) {chain}{bs}")
                        lines.append(footer_links())
                        refresh_kb = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "🔄 Refresh",
                                        callback_data="trending_refresh",
                                    )
                                ],
                                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                            ]
                        )
                        await query.edit_message_text(
                            "\n".join(lines),
                            parse_mode=ParseMode.HTML,
                            reply_markup=refresh_kb,
                            disable_web_page_preview=True,
                        )
                        return
        except Exception as e:
            logger.warning(f"trending_refresh failed: {e}")
        await query.edit_message_text(
            "❌ Could not refresh trending data.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "news_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("📰 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        articles = []
        source_label = "Backend"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5")
                if r.status_code == 200:
                    body = r.json()
                    articles = body.get("items", [])
        except Exception as e:
            logger.warning("news_refresh backend failed: %s", e)
        if not articles:
            cg_items = await _fetch_cg_trending_news()
            if cg_items:
                articles = cg_items
                source_label = "CoinGecko"
        if articles:
            lines = ["📰 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
            for a in articles[:5]:
                title = (a.get("title") or "Untitled")[:100]
                source = a.get("source", "News")
                published = _time_ago(a.get("published_at") or a.get("publishedAt"))
                pub_str = f" · {published}" if published else ""
                url = a.get("url") or a.get("link") or ""
                title_linked = f'<a href="{url}">{title}</a>' if url else title
                lines.append(f"📰 {title_linked}\n  <i>{source}</i>{pub_str}")
            lines.append(footer_links())
            news_kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="news_refresh")],
                    [InlineKeyboardButton("📢 Alerts Channel", url=TELEGRAM_CHANNEL)],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )
            with contextlib.suppress(Exception):
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=news_kb,
                    disable_web_page_preview=True,
                )
        else:
            await query.edit_message_text(
                "📰 <b>Crypto News</b>\n\nNews feed temporarily unavailable.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("dismiss_alert_"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                "✅ Alert dismissed.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await query.answer("Alert dismissed.")
    elif data == "menu_watchlist":
        items = db.get_watchlist(user.id)
        if not items:
            await query.edit_message_text(
                "👀 Watchlist is empty.\nUse /watch <code>address</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        else:
            text = "👀 <b>Watchlist</b>\n\n" + "\n".join(
                f"• {w.get('symbol', '???')} <code>{short_addr(w['token_address'])}</code>"
                for w in items
            )
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb())
    elif data == "noop":
        await query.answer()
    elif data == "menu_refer":
        u = db.get_or_create_user(user.id)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{u.get('referral_code', '')}"
        await query.edit_message_text(
            f"🤝 <b>Refer Friends</b>\n\nShare your link:\n<code>{link}</code>\n\nBoth get 5 bonus scans!",
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
                f"🔍 Use /scan {parts[1]} to run a full scan.",
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
            f"⭐ <b>Buy Stars</b>\n\n"
            f"Your balance: ⭐{u.get('stars_balance', 0):,}\n\n"
            f"Purchase Stars and spend them on scans, subscriptions, "
            f"and premium features!\n\n"
            f"Select a pack below 👇",
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
        except Exception as e:
            logger.error(f"Buy stars invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )
    elif data.startswith("deep_"):
        # deep_{addr}_{chain}
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.answer("Invalid request.", show_alert=True)
            return
        addr, chain = parts[1], parts[2]
        token = secrets.token_hex(8)
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
                title="🔬 Deep Token Scan",
                description=f"Complete forensic analysis of {short_addr(addr)} on {chain}.",
                payload=f"deep_{token}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice("Deep Scan", SCAN_PACKS["deep_scan"]["stars"])],
            )
            await query.answer()
        except Exception as e:
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
                    f"• {tier['scans_per_week']} scans/week\n"
                    f"• {tier['ai_msgs_per_week']} AI messages/week\n"
                    f"• All premium features"
                ),
                payload=f"sub_{tier_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(f"{tier['name']} - 30 days", tier["price_stars"])],
            )
            await query.answer()
        except Exception as e:
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
                description=f"Purchase {pack['name']} — never expires!",
                payload=f"topup_{pack_key}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(pack["name"], pack["stars"])],
            )
            await query.answer()
        except Exception as e:
            logger.error(f"Topup invoice error: {e}")
            await query.answer(
                "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
                show_alert=True,
            )


# ══════════════════════════════════════════════
# PAYMENT HANDLER
# ══════════════════════════════════════════════
async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload
    # Validate payload format — accept known prefixes only
    valid_prefixes = ("buystars_", "topup_", "sub_", "deep_")
    if any(payload.startswith(p) for p in valid_prefixes):
        await query.answer(ok=True)
    else:
        logger.warning("Unknown invoice payload: %s", payload)
        await query.answer(ok=False, error_message="Invalid invoice.")


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    payload = payment.invoice_payload
    charge_id = payment.telegram_payment_charge_id
    total = payment.total_amount

    # ── Route by payload prefix ──
    if payload.startswith("buystars_"):
        # Purchase Stars → credit local balance
        pack_key = payload[9:]
        pack = BUY_STARS_PACKS.get(pack_key)
        stars_to_add = pack["stars"] if pack else total
        db.add_stars(user.id, stars_to_add, "purchase", charge_id)
        await update.message.reply_text(
            f"✅ <b>Stars Purchased!</b>\n\n"
            f"⭐ +{stars_to_add:,} Stars added to your balance.\n"
            f"Use /topup to spend them on scans and subscriptions!",
            parse_mode=ParseMode.HTML,
        )

    elif payload.startswith("topup_"):
        # Top-up purchase → credit bonus scans/ai_msgs
        pack_key = payload[6:]
        pack = TOP_UP_PACKS.get(pack_key)
        if pack:
            conn = db.get_db()
            if pack["type"] == "scans":
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + ? WHERE user_id = ?",
                    (pack["amount"], user.id),
                )
            elif pack["type"] == "ai_msgs":
                conn.execute(
                    "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs + ? WHERE user_id = ?",
                    (pack["amount"], user.id),
                )
            conn.execute(
                "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user.id, -pack["stars"], f"topup_{pack_key}", charge_id, int(time.time())),
            )
            conn.commit()
            conn.close()
            await update.message.reply_text(
                f"✅ <b>Purchase Successful!</b>\n\n"
                f"+{pack['amount']} bonus {pack['type'].replace('_', ' ')}\n"
                f"Cost: ⭐{pack['stars']}\n\n"
                f"These never expire!",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "✅ Payment received but pack not found. Contact support.",
                parse_mode=ParseMode.HTML,
            )

    elif payload.startswith("sub_"):
        # Subscription purchase → activate tier
        tier_key = payload[4:]
        tier = TIERS.get(tier_key)
        if tier and tier_key != "free":
            db.activate_tier(user.id, tier_key, 30)
            tc = TIERS[tier_key]
            await update.message.reply_text(
                f"🎉 <b>Welcome to {tc['emoji']} {tc['name']}!</b>\n\n"
                f"Your {tc['name']} tier is now active for 30 days.\n"
                f"· {tc['scans_per_week']} scans/week\n"
                f"· {tc['ai_msgs_per_week']} AI messages/week\n"
                f"· All premium features unlocked!\n\n"
                f"Use /account to see your dashboard.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "✅ Payment received but tier not found. Contact support.",
                parse_mode=ParseMode.HTML,
            )

    elif payload.startswith("deep_"):
        # Deep scan purchase → run enhanced scan
        token = payload[5:]
        pending = _pending_deep_scans.pop(token, None)
        if pending and pending["user_id"] == user.id:
            msg = await update.message.reply_text(
                f"🔬 <b>Deep Scan Started</b>\n\n"
                f"Running enhanced analysis on <code>{short_addr(pending['address'])}</code>...",
                parse_mode=ParseMode.HTML,
            )
            try:
                result = await scan_token(pending["address"], pending["chain"])
                report = format_scan_report(result)
                report = f"🔬 <b>Deep Token Scan</b>\n{sep()}\n\n" + report
                db.log_scan(
                    user.id, pending["address"], result["chain"], "deep", result["risk_score"]
                )
                await msg.edit_text(
                    report,
                    parse_mode=ParseMode.HTML,
                    reply_markup=scan_result_kb(pending["address"], result["chain"]),
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"Deep scan payment error: {e}")
                await msg.edit_text(
                    f"❌ Deep scan failed: {str(e)[:200]}\n\nContact support for help.",
                    parse_mode=ParseMode.HTML,
                )
        else:
            await update.message.reply_text(
                "✅ Payment received but the scan request expired. Please run /scan again.",
                parse_mode=ParseMode.HTML,
            )

    else:
        # Legacy fallback — just credit Stars
        db.add_stars(user.id, total, "payment", charge_id)
        await update.message.reply_text(
            f"✅ <b>Payment Received!</b>\n\n"
            f"⭐ {total} Stars added to your balance.\n"
            f"Use /topup to spend them.",
            parse_mode=ParseMode.HTML,
        )


# ══════════════════════════════════════════════
# GLOBAL ERROR HANDLER
# ══════════════════════════════════════════════
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again.\nIf the issue persists, contact support.",
                parse_mode=ParseMode.HTML,
            )


# ══════════════════════════════════════════════
# BOT PROFILE SETUP (BotFather API)
# ══════════════════════════════════════════════
async def setup_bot_profile(app: Application) -> None:
    """Set bot description, about text, and command list via BotFather API."""
    bot = app.bot
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Bot description set")
    except Exception as e:
        logger.warning(f"Could not set description: {e}")
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Bot short description set")
    except Exception as e:
        logger.warning(f"Could not set short description: {e}")
    try:
        commands = [
            BotCommand("start", "Welcome & quick start guide"),
            BotCommand("help", "Full command reference"),
            BotCommand("scan", "Scan token for scams & risks"),
            BotCommand("wallet", "Wallet forensics & analysis"),
            BotCommand("ta", "Technical analysis & signals"),
            BotCommand("compare", "Compare two tokens side-by-side"),
            BotCommand("quick", "Quick token price check"),
            BotCommand("trending", "Hot tokens right now"),
            BotCommand("rugcheck", "Rug pull red flag checklist"),
            BotCommand("watch", "Add token to watchlist"),
            BotCommand("watchlist", "View your watchlist"),
            BotCommand("alerts", "Price & activity alerts"),
            BotCommand("account", "Your dashboard & usage"),
            BotCommand("pricing", "Subscription plans"),
            BotCommand("topup", "Buy extra scans (no expiry)"),
            BotCommand("buystars", "Buy Telegram Stars ⭐"),
            BotCommand("refer", "Invite friends, earn scans"),
            BotCommand("scamschool", "Learn about crypto scams"),
            BotCommand("chart", "Price chart for any token"),
            BotCommand("portfolio", "Scan wallet token holdings"),
            BotCommand("simulate", "Simulate buy/sell trade"),
            BotCommand("smartmoney", "Detect smart money wallets"),
            BotCommand("rugmap", "Wallet cluster visualization"),
            BotCommand("news", "Latest crypto news"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Bot commands registered")
    except Exception as e:
        logger.warning(f"Could not set commands: {e}")


# ══════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════
async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNELS.get("main"):
        return
    topic_key = random.choice(list(SCAM_SCHOOL_TOPICS.keys()))
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    text = (
        f"🛡️ <b>Daily Scam Tip</b>\n{sep()}\n\n"
        f"{topic['content']}\n\n"
        f"📚 Learn more: /scamschool\n"
        f"🔍 Scan tokens: @RugMunchBot\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a>'
    )
    try:
        await ctx.bot.send_message(
            CHANNELS["main"], text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Daily tip failed: {e}")


# ══════════════════════════════════════════════
# PRICE ALERT ENGINE
# ══════════════════════════════════════════════
DIRECTION_LABELS = {
    "above": "📈 above",
    "below": "📉 below",
    "change_5": "📊 5% change",
    "any": "🔄 significant change",
}


async def _fetch_prices_batch(addresses: list[str]) -> dict[str, float]:
    """Fetch current USD prices for a list of token addresses from DexScreener.

    Returns dict mapping address → current price (0.0 if not found).
    Batches all addresses into a single request.
    """
    prices: dict[str, float] = {}
    if not addresses:
        return prices
    try:
        # DexScreener batch: comma-separated addresses
        batch = ",".join(addresses)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{batch}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                for pair in pairs:
                    addr = pair.get("baseToken", {}).get("address", "").lower()
                    price = float(pair.get("priceUsd", 0))
                    if addr and price > 0 and addr not in prices:
                        prices[addr] = price
    except Exception as e:
        logger.warning(f"Batch price fetch error: {e}")
    return prices


def _check_alert_condition(
    current_price: float,
    alert_price: float,
    direction: str,
) -> bool:
    """Determine if an alert condition is met."""
    if current_price <= 0 or alert_price <= 0:
        return False
    if direction == "above":
        return current_price >= alert_price
    elif direction == "below":
        return current_price <= alert_price
    elif direction == "change_5":
        change_pct = abs((current_price - alert_price) / alert_price) * 100
        return change_pct >= 5.0
    elif direction == "any":
        change_pct = abs((current_price - alert_price) / alert_price) * 100
        return change_pct >= 2.0
    return False


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: check all active price alerts and fire notifications.

    Runs every ALERT_CHECK_INTERVAL seconds via APScheduler.
    - Queries all active watchlist entries with alert rules
    - Batches DexScreener calls by token address
    - Fires DM notifications for triggered alerts
    - Respects per-user rate limit (1 alert / 5 min)
    - Marks triggered alerts as inactive with timestamp
    """
    logger.info("🔔 Checking price alerts...")

    # 1. Fetch all active alerts
    active_alerts = db.get_active_alerts()
    if not active_alerts:
        logger.info("No active alerts to check.")
        return

    logger.info(f"Found {len(active_alerts)} active alert(s) to check.")

    # 2. Group by token address for batch fetching
    #    (deduplicate since multiple users may watch the same token)
    unique_addresses = list({a["token_address"].lower() for a in active_alerts})
    current_prices = await _fetch_prices_batch(unique_addresses)

    if not current_prices:
        logger.warning("No prices fetched from DexScreener, skipping alert check.")
        return

    # 3. Evaluate each alert
    triggered: list[dict] = []
    for alert in active_alerts:
        addr = alert["token_address"].lower()
        current_price = current_prices.get(addr, 0.0)
        if current_price <= 0:
            continue

        fired = _check_alert_condition(
            current_price=current_price,
            alert_price=alert["alert_price"],
            direction=alert["alert_direction"],
        )
        if fired:
            triggered.append(alert)

    if not triggered:
        logger.info("No alerts triggered.")
        return

    logger.info(f"{len(triggered)} alert(s) triggered, sending notifications...")

    # 4. Send DM to each user (respecting rate limit)
    sent_count = 0
    rate_limited = 0
    for alert in triggered:
        user_id = alert["user_id"]
        if not _check_alert_rate_limit(user_id):
            rate_limited += 1
            continue

        symbol = alert.get("symbol") or "???"
        addr = alert["token_address"]
        current_price = current_prices.get(addr.lower(), 0.0)
        target_price = alert["alert_price"]
        direction = alert["alert_direction"]

        # Calculate change percentage
        if target_price > 0:
            change_pct = abs((current_price - target_price) / target_price) * 100
        else:
            change_pct = 0.0

        direction_emoji = "📈" if direction == "above" else "📉" if direction == "below" else "📊"
        direction_label = DIRECTION_LABELS.get(direction, direction)

        text = (
            f"🔔 <b>Price Alert Triggered!</b>\n{sep()}\n\n"
            f"<b>{symbol}</b>\n"
            f"<code>{short_addr(addr)}</code>\n\n"
            f"💰 <b>Current Price:</b> {fmt_number(current_price)}\n"
            f"🎯 <b>Target Price:</b> {fmt_number(target_price)}\n"
            f"{direction_emoji} <b>Direction:</b> {direction_label}\n"
            f"📊 <b>Change:</b> {change_pct:.1f}%\n"
            f"{footer_links()}"
        )

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Dismiss", callback_data=f"dismiss_alert_{alert['id']}")],
            ]
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Failed to send alert to user {user_id}: {e}")
            # Don't mark as triggered if we couldn't notify
            continue

        # Mark alert as triggered
        db.mark_alert_triggered(alert["id"])

    logger.info(
        f"Alert check complete: {sent_count} sent, {rate_limited} rate-limited, "
        f"{len(triggered) - sent_count - rate_limited} failed"
    )


# ══════════════════════════════════════════════
# /ALERT COMMAND — Set quick price alert
# ══════════════════════════════════════════════
async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a price alert: /alert <ABOVE|BELOW> <address> <price>

    Examples:
      /alert ABOVE 0x6982508145454Ce325dDbE47a25d4ec3d2311933 0.005
      /alert BELOW EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.85
    """
    user = update.effective_user
    if user is None:
        return

    # Tier check — alerts require Scout+
    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(
            "🔔 <b>Price Alerts</b>\n\n"
            "Price alerts require <b>Scout tier</b> or higher.\n\n"
            "🔍 <b>Scout ($29.99/mo)</b> — 10 watchlist slots + alerts\n"
            "🎯 <b>Hunter ($49.99/mo)</b> — 50 slots + real-time alerts\n"
            "👑 <b>Pro ($99.99/mo)</b> — unlimited + custom rules",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    if len(ctx.args) < 2:
        await update.message.reply_text(
            "🔔 <b>Set Price Alert</b>\n\n"
            "Usage: /alert <code>ABOVE|BELOW|CHANGE_5|ANY</code> <code>address</code> <price>\n\n"
            "<b>Examples:</b>\n"
            "  /alert ABOVE 0x6982508145454Ce325dDbE47a25d4ec3d2311933 0.005\n"
            "  /alert BELOW EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.85\n"
            "  /alert CHANGE_5 0x... — 5% move in either direction\n"
            "  /alert ANY 0x... 0.01 — alert on 2%+ change\n\n"
            "<i>Add the token to your watchlist first with /watch</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    direction_raw = ctx.args[0].upper()
    addr = ctx.args[1]
    price_str = ctx.args[2] if len(ctx.args) > 2 else None

    # Validate direction
    valid_directions = {"ABOVE", "BELOW", "CHANGE_5", "ANY"}
    if direction_raw not in valid_directions:
        await update.message.reply_text(
            f"❌ Invalid direction. Use one of: {', '.join(d.lower() for d in valid_directions)}",
            parse_mode=ParseMode.HTML,
        )
        return

    # Validate price for ABOVE/BELOW
    if direction_raw in ("ABOVE", "BELOW") and not price_str:
        await update.message.reply_text(
            "❌ Please provide a target price for ABOVE/BELOW alerts.\n"
            "Example: /alert ABOVE <code>address</code> 0.005",
            parse_mode=ParseMode.HTML,
        )
        return

    chain = detect_chain(addr)
    if chain == "unknown":
        await update.message.reply_text(
            "❌ Could not detect chain. Ensure it's a valid EVM (0x...) or Solana address.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Parse price
    alert_price: float | None = None
    if price_str:
        try:
            alert_price = float(price_str)
            if alert_price <= 0:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(
                "❌ Invalid price. Use a positive number (e.g., 0.005, 1.50, 42000).",
                parse_mode=ParseMode.HTML,
            )
            return

    # Check if token is already in watchlist
    existing = db.get_watchlist(user.id)
    existing_entry = next(
        (w for w in existing if w["token_address"].lower() == addr.lower()),
        None,
    )

    if existing_entry:
        # Update existing entry with alert
        conn = db.get_db()
        conn.execute(
            "UPDATE watchlists SET alert_price = ?, alert_direction = ?, is_active = 1, alert_triggered_at = NULL WHERE id = ?",
            (alert_price, direction_raw.lower(), existing_entry["id"]),
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"✅ <b>Alert Updated</b>\n\n"
            f"Token: <code>{short_addr(addr)}</code>\n"
            f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
            f"Use /alerts to see all your active alerts.",
            parse_mode=ParseMode.HTML,
        )
    else:
        # Add to watchlist with alert
        ok = db.add_watchlist(
            user.id,
            addr,
            chain,
            symbol=None,
            alert_price=alert_price,
            alert_dir=direction_raw.lower(),
        )
        if ok:
            await update.message.reply_text(
                f"✅ <b>Alert Set</b>\n\n"
                f"Token: <code>{short_addr(addr)}</code>\n"
                f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
                f"You'll be notified when this condition is met.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "❌ Could not set alert. Watchlist limit reached or already exists.",
                parse_mode=ParseMode.HTML,
            )


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main() -> None:
    if not BOT_TOKEN:
        logger.error("RUGMUNCH_BOT_TOKEN not set!")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("rugcheck", cmd_rugcheck))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("pricing", cmd_pricing))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("buystars", cmd_buystars))
    app.add_handler(CommandHandler("scamschool", cmd_scamschool))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("alert", cmd_alert))

    # ── Newly Wired Commands ──
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("simulate", cmd_simulate))
    app.add_handler(CommandHandler("smartmoney", cmd_smartmoney))
    app.add_handler(CommandHandler("rugmap", cmd_rugmap))

    # ── Owner Commands ──
    app.add_handler(CommandHandler("admin_stats", cmd_admin_stats))
    app.add_handler(CommandHandler("admin_set_tier", cmd_admin_set_tier))
    app.add_handler(CommandHandler("admin_broadcast", cmd_admin_broadcast))
    app.add_handler(CommandHandler("admin_ban", cmd_admin_ban))

    # ── Inline Query ──
    app.add_handler(InlineQueryHandler(handle_inline))

    # ── Callbacks & Messages ──
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ── Payments ──
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # ── Error Handler ──
    app.add_error_handler(error_handler)

    # ── Scheduled Jobs ──
    job_queue = app.job_queue
    job_queue.run_daily(daily_scam_tip, dtime(hour=14, minute=0, tzinfo=UTC))
    job_queue.run_repeating(check_alerts, interval=ALERT_CHECK_INTERVAL, first=10)

    # ── Startup: health check + bot profile ──
    async def startup(app: Application) -> None:
        await check_backend_health()
        await setup_bot_profile(app)

    app.post_init = startup

    logger.info("🛡️ CryptoRugMunch Bot v6 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
