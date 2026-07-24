#!/usr/bin/env python3
"""
CryptoRugMunch Bot - Configuration
===================================
Tiers, channels, API endpoints, DEX ref links, and constants.
"""
from __future__ import annotations

import os
import subprocess

import httpx

# ══════════════════════════════════════════════
# BOT TOKEN (gopass → env fallback)
# ══════════════════════════════════════════════
def _get_token(gopass_path: str, env_keys: list[str]) -> str:
    """Fetch token from gopass at runtime, falling back to env vars."""
    try:
        token = (
            subprocess.check_output(
                ["gopass", "show", gopass_path],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    for key in env_keys:
        token = os.getenv(key)
        if token:
            return token
    return ""

BOT_TOKEN = _get_token(
    "rmi/infra/telegram_rugmunch_bot_token",
    ["RUGMUNCH_BOT_TOKEN", "TELEGRAM_RUGMUNCH_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
)
OWNER_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ALLOWED_USERS", "7075336557").split(",")
    if x.strip()
}
BOT_USERNAME = "RugMunchBot"

# ══════════════════════════════════════════════
# BRANDING & SOCIAL
# ══════════════════════════════════════════════
WEBSITE_URL = "https://rugmunch.io"
WEB_APP_URL = f"{WEBSITE_URL}/scanner"
TWITTER_URL = "https://x.com/RugMunch"
TELEGRAM_CHANNEL = "https://t.me/RugMunchAlerts"
TELEGRAM_GROUP = "https://t.me/RugMunchChat"
SUPPORT_EMAIL = "biz@rugmunch.io"

# Bot profile (set via BotFather API on startup)
BOT_DESCRIPTION = (
    "🛡️ RugMunch Intelligence - The Bloomberg Terminal of Shitcoins\n\n"
    "Scan any token across 77+ chains for honeypots, rug pulls, bundles, "
    "fake volume, and dev wallet history. AI-powered risk scoring with "
    "real-time alerts.\n\n"
    "🔍 Multi-chain token scanning\n"
    "👛 Wallet forensics & smart money tracking\n"
    "📊 Technical analysis & trending tokens\n"
    "🚨 Real-time scam detection & alerts\n"
    "📚 Free crypto scam education\n\n"
    "🌐 rugmunch.io"
)
BOT_SHORT_DESCRIPTION = (
    "🛡️ Crypto scam detection & token intelligence across 77+ chains. Free tier available."
)

# ══════════════════════════════════════════════
# API ENDPOINTS
# ══════════════════════════════════════════════
# Bot runs on HOST (not in Docker). Backend runs in Docker.
#   - Host → backend: http://localhost:8000
#   - Inside Docker → backend: http://rmi-backend:8000
_IN_DOCKER = os.getenv("DOCKER_CONTAINER") or os.path.exists("/.dockerenv")
BACKEND_HOST = "rmi-backend:8000" if _IN_DOCKER else "localhost:8000"
BACKEND_URL = os.getenv("RMI_BACKEND_URL", f"http://{BACKEND_HOST}")

async def check_backend_health() -> None:
    """Ping backend /health on startup. Logs WARNING if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BACKEND_URL}/health")
            if r.status_code == 200:
                from app.core.logging import get_logger

                get_logger("rmi.config").info("Backend reachable at %s", BACKEND_URL)
            else:
                from app.core.logging import get_logger

                get_logger("rmi.config").warning(
                    "Backend at %s returned status %d", BACKEND_URL, r.status_code
                )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        from app.core.logging import get_logger

        get_logger("rmi.config").warning("Backend UNREACHABLE at %s: %s", BACKEND_URL, exc)

# Alert engine
ALERT_CHECK_INTERVAL = 300  # seconds between alert checks (5 min)

# External APIs
DEXSCREENER = "https://api.dexscreener.com/latest/dex"
COINGECKO = "https://api.coingecko.com/api/v3"  # price history & trending
GOPLUS = "https://api.gopluslabs.com/api/v1"  # EVM token security scanner
HONEYPOT = "https://api.honeypot.is/v2"  # primary EVM security scanner

# ══════════════════════════════════════════════
# SUBSCRIPTION TIERS (Weekly Allotments)
# ══════════════════════════════════════════════
# Weekly model: resets every Monday 00:00 UTC.
# Prevents day-one dump abuse and smooths API load.
TIERS = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "scans_per_week": 25,
        "ai_msgs_per_week": 15,
        "price_monthly": 0,
        "features": [
            "25 scans/week",
            "15 AI questions/week",
            "Basic risk score",
            "Contract safety check",
            "Liquidity overview",
            "Top 10 holders",
            "Community access",
        ],
    },
    "scout": {
        "name": "Scout",
        "emoji": "🔍",
        "scans_per_week": 150,
        "ai_msgs_per_week": 75,
        "price_monthly": 29.99,
        "price_stars": 2999,
        "features": [
            "150 scans/week",
            "75 AI questions/week",
            "Advanced risk scoring",
            "Bundle detection",
            "Fake volume detection",
            "Dev wallet finder",
            "Holder trend analysis",
            "Smart money alerts",
            "Watchlist (10 tokens)",
            "Export reports",
        ],
    },
    "hunter": {
        "name": "Hunter",
        "emoji": "🎯",
        "scans_per_week": 400,
        "ai_msgs_per_week": 250,
        "price_monthly": 49.99,
        "price_stars": 4999,
        "features": [
            "400 scans/week",
            "250 AI questions/week",
            "Everything in Scout, plus:",
            "Full wallet forensics",
            "First buyer analysis",
            "Exchange flow tracking",
            "Fresh wallet detection",
            "Shill detection",
            "Social sentiment scan",
            "Watchlist (50 tokens)",
            "Real-time alerts",
            "TA chart generation",
        ],
    },
    "pro": {
        "name": "Pro",
        "emoji": "👑",
        "scans_per_week": 1000,
        "ai_msgs_per_week": 500,
        "price_monthly": 99.99,
        "price_stars": 9999,
        "features": [
            "1,000 scans/week",
            "500 AI questions/week",
            "Everything in Hunter, plus:",
            "Smart money watch (live)",
            "Cross-chain tracking",
            "Custom alert rules",
            "API access",
            "Priority support",
            "Early feature access",
            "Unlimited watchlists",
            "Multi-wallet tracking",
            "White-label reports",
        ],
    },
}

# ══════════════════════════════════════════════
# TOP-UP PACKS (Buy extra when you run out)
# ══════════════════════════════════════════════
# One-time purchases via Telegram Stars. No expiry -
# top-up balance carries over forever until used.
TOP_UP_PACKS = {
    "scans_10": {
        "name": "10 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 10,
        "stars": 75,
    },
    "scans_50": {
        "name": "50 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 50,
        "stars": 300,
    },
    "scans_100": {
        "name": "100 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 100,
        "stars": 500,
    },
    "ai_10": {
        "name": "10 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 10,
        "stars": 100,
    },
    "ai_50": {
        "name": "50 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 50,
        "stars": 400,
    },
    "ai_100": {
        "name": "100 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 100,
        "stars": 700,
    },
}

# ══════════════════════════════════════════════
# BUY STARS PACKS (Purchase Stars via Telegram invoice)
# ══════════════════════════════════════════════
# Users pay Telegram Stars (XTR) and receive Stars credited
# to their local balance. ~$0.01 per Star.
BUY_STARS_PACKS = {
    "stars_100": {
        "name": "100 Stars",
        "emoji": "⭐",
        "stars": 100,
    },
    "stars_500": {
        "name": "500 Stars",
        "emoji": "⭐",
        "stars": 500,
    },
    "stars_1000": {
        "name": "1,000 Stars",
        "emoji": "⭐",
        "stars": 1000,
    },
    "stars_2500": {
        "name": "2,500 Stars",
        "emoji": "⭐",
        "stars": 2500,
    },
    "stars_5000": {
        "name": "5,000 Stars",
        "emoji": "⭐",
        "stars": 5000,
    },
}

# ══════════════════════════════════════════════
# SCAN PACKS (One-time deep analysis purchases)
# ══════════════════════════════════════════════
SCAN_PACKS = {
    "deep_scan": {
        "name": "🔬 Deep Token Scan",
        "description": "Complete forensic analysis of ONE token",
        "includes": [
            "Price history",
            "Holder RugMap",
            "Wallet clusters",
            "Scam indicators",
            "AI prediction",
            "Dev history",
        ],
        "stars": 250,
    },
    "wallet_forensic": {
        "name": "🕵️ Wallet Forensic Report",
        "description": "Full wallet investigation - P&L, connections, history",
        "includes": [
            "Transaction history",
            "Counterparty analysis",
            "P&L breakdown",
            "Bundle links",
            "Scam DB check",
        ],
        "stars": 400,
    },
    "bundle_investigation": {
        "name": "🕸️ Bundle Investigation",
        "description": "Full cluster analysis - find every connected wallet",
        "includes": [
            "Funding source map",
            "Timing correlation",
            "Wash trade detection",
            "Holder overlap",
            "Exit strategy clues",
        ],
        "stars": 500,
    },
}

# ══════════════════════════════════════════════
# DEX REFERRAL LINKS
# ══════════════════════════════════════════════
# These generate revenue when users trade through our links.
# Format: {platform: {url_template, chains, emoji, description}}
DEX_REF_LINKS = {
    "jupiter": {
        "name": "Jupiter",
        "emoji": "🪐",
        "url": "https://jup.ag/swap/SOL-{token}?ref=rugmunch",
        "chains": ["solana"],
        "description": "Best Solana DEX aggregator",
    },
    "photon": {
        "name": "Photon",
        "emoji": "⚡",
        "url": "https://photon-sol.tinyastro.io/en/lp/{token}?handle=rugmunch",
        "chains": ["solana", "ethereum", "base"],
        "description": "Fast Solana trading terminal",
    },
    "bullx": {
        "name": "BullX",
        "emoji": "🐂",
        "url": "https://neo.bullx.io/terminal?chain={chain}&address={token}&ref=rugmunch",
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Multi-chain trading bot",
    },
    "gmgn": {
        "name": "GMGN.ai",
        "emoji": "📊",
        "url": "https://gmgn.ai/sol/token/{token}?ref=rugmunch",
        "chains": ["solana", "ethereum", "base"],
        "description": "Smart money tracking + trading",
    },
    "birdeye": {
        "name": "Birdeye",
        "emoji": "🦅",
        "url": "https://birdeye.so/token/{token}?ref=rugmunch",
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Token analytics & charts",
    },
    "oneinch": {
        "name": "1inch",
        "emoji": "🦄",
        "url": "https://app.1inch.io/#/{chain_id}/simple/swap/ETH/{token}?ref=rugmunch",
        "chains": ["ethereum", "base", "arbitrum", "bsc", "polygon", "avalanche"],
        "description": "Best EVM DEX aggregator",
    },
    "pancakeswap": {
        "name": "PancakeSwap",
        "emoji": "🥞",
        "url": "https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency={token}&ref=rugmunch",
        "chains": ["bsc", "ethereum", "arbitrum", "base"],
        "description": "BSC's #1 DEX",
    },
}

# Chain ID mapping for 1inch
CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "avalanche": 43114,
    "optimism": 10,
    "base": 8453,
    "fantom": 250,
}

# ══════════════════════════════════════════════
# CHANNELS
# ══════════════════════════════════════════════
CHANNELS = {
    "main": "-1002056885429",
    "news": "-1003982391578",
    "alerts": "-1003818352164",
    "alpha": "-1003762675055",
    "scans": "-1003937506770",
}

# ══════════════════════════════════════════════
# RISK VISUALIZATION
# ══════════════════════════════════════════════
RISK_COLORS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "safe": "✅",
    "unknown": "⚪",
}

def risk_bar(score: float, length: int = 10) -> str:
    """Generate a visual risk bar (0-100 scale)."""
    filled = int((score / 100) * length)
    if score >= 80:
        return "🔴" * filled + "⬜" * (length - filled) + f" {score}% DANGER"
    elif score >= 60:
        return "🟠" * filled + "⬜" * (length - filled) + f" {score}% RISKY"
    elif score >= 40:
        return "🟡" * filled + "⬜" * (length - filled) + f" {score}% CAUTION"
    elif score >= 20:
        return "🟢" * filled + "⬜" * (length - filled) + f" {score}% LOW"
    else:
        return "✅" * filled + "⬜" * (length - filled) + f" {score}% SAFE"

def threat_indicator(label: str, found: bool) -> str:
    return f"{'🔴' if found else '✅'} {label}"

# ══════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════
def fmt_number(n) -> str:
    """Format large numbers: 1.2M, 450K, etc."""
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    elif n >= 1e6:
        return f"${n / 1e6:.2f}M"
    elif n >= 1e3:
        return f"${n / 1e3:.1f}K"
    elif n >= 1:
        return f"${n:.4f}"
    elif n >= 0.01:
        return f"${n:.6f}"
    else:
        return f"${n:.10f}"

def fmt_pct(n) -> str:
    if n is None:
        return "N/A"
    try:
        return f"{float(n):.2f}%"
    except (ValueError, TypeError):
        return str(n)

def fmt_chain(chain: str) -> str:
    """Return chain emoji + name."""
    emojis = {
        "solana": "◎",
        "ethereum": "⟠",
        "bsc": "🔶",
        "polygon": "🟣",
        "arbitrum": "🔵",
        "optimism": "🔴",
        "base": "🔷",
        "avalanche": "🔺",
        "fantom": "👻",
        "tron": "🔻",
        "bitcoin": "₿",
    }
    return f"{emojis.get(chain.lower(), '⛓️')} {chain.capitalize()}"

# ══════════════════════════════════════════════
# SCAM SCHOOL TOPICS
# ══════════════════════════════════════════════
SCAM_SCHOOL_TOPICS = {
    "honeypot": {
        "title": "🍯 Honeypot Tokens",
        "content": "A honeypot lets you buy but prevents you from selling. The contract has hidden code that only allows whitelisted addresses to sell.\n\n🔍 *How to spot:*\n• Can buy but can't sell\n• Only 1-2 sellers ever\n• Contract has sell restrictions\n• Honeypot.is flags it\n\n🛡️ *Protection:* Always test with a small amount first, and use /scan before buying.",
    },
    "rugpull": {
        "title": "🏃 Rug Pulls",
        "content": "Developers drain all liquidity from a token, making it worthless. Can happen via:\n\n• Removing LP tokens\n• Minting infinite tokens\n• Hidden sell functions\n• Blacklisting buyers\n\n🔍 *How to spot:*\n• Unlocked liquidity\n• Unrenounced contract\n• Dev holds >20% supply\n• New wallet, no history\n\n🛡️ *Protection:* Check /scan for LP lock status and dev wallet analysis.",
    },
    "bundling": {
        "title": "📦 Bundle Scams",
        "content": "Devs use multiple wallets to create fake organic activity. They buy from 10-50 wallets funded from the same source.\n\n🔍 *How to spot:*\n• Multiple wallets funded from same CEX/bridge\n• Identical buy amounts\n• Same-timestamp transactions\n• Wallets only hold this one token\n\n🛡️ *Protection:* Use /scan bundle detection to trace funding sources.",
    },
    "wash_trading": {
        "title": "🔄 Wash Trading",
        "content": "Artificially inflating volume by buying and selling between own wallets.\n\n🔍 *How to spot:*\n• High volume, low unique traders\n• Same wallets trading back and forth\n• Round number trade amounts\n• Volume doesn't match market cap\n\n🛡️ *Protection:* /scan checks volume authenticity.",
    },
    "fresh_wallets": {
        "title": "🆕 Fresh Wallet Red Flags",
        "content": "When most holders are brand new wallets (created <7 days ago), it often means the dev created fake holders.\n\n🔍 *How to spot:*\n• >50% wallets <7 days old\n• Wallets only hold 1 token\n• No transaction history before buying\n• Similar naming patterns\n\n🛡️ *Protection:* /scan shows wallet age distribution.",
    },
    "exchange_flow": {
        "title": "🏦 Exchange Flow Analysis",
        "content": "Tracking when large holders move tokens to exchanges signals potential dumps.\n\n🔍 *How to spot:*\n• Top holders sending to CEX wallets\n• Increasing exchange deposits\n• Unusual transfer patterns\n• Pre-scheduled dumps\n\n🛡️ *Protection:* Hunter+ tiers get real-time exchange flow alerts.",
    },
}
