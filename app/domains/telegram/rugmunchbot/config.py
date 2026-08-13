#!/usr/bin/env python3
"""
CryptoRugMunch Bot - Configuration
===================================
Tiers, channels, API endpoints, DEX ref links, and constants.
"""

from __future__ import annotations

import os
import subprocess
from typing import TypedDict

import httpx


class TierDefinition(TypedDict, total=False):
    name: str
    emoji: str
    scans_per_week: int
    ai_msgs_per_week: int
    price_monthly: int | float
    price_stars: int
    features: list[str]


class StarsPack(TypedDict):
    name: str
    emoji: str
    stars: int


class TopUpPack(TypedDict):
    name: str
    emoji: str
    type: str
    amount: int
    stars: int


class ScanPack(TypedDict):
    name: str
    description: str
    includes: list[str]
    stars: int


# ══════════════════════════════════════════════
# BOT TOKEN (gopass → env fallback)
# ══════════════════════════════════════════════
def _get_token(gopass_path: str, env_keys: list[str]) -> str:
    """Fetch token from gopass at runtime, falling back to env vars."""
    try:
        token = (
            subprocess.check_output(["gopass", "show", gopass_path], stderr=subprocess.DEVNULL, timeout=5, shell=False)
            .decode()
            .strip()
        )
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    for key in env_keys:
        env_token = os.getenv(key)
        if env_token:
            return env_token
    return ""


BOT_TOKEN = _get_token(
    "rmi/rugmunchbot/bot_token",
    ["RUGMUNCH_BOT_TOKEN", "TELEGRAM_RUGMUNCH_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
)
OWNER_IDS = {int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_USERS", "7075336557").split(",") if x.strip()}
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
    "🛡️ Rug Munch Intelligence - The Bloomberg Terminal of Shitcoins\n\n"
    "Scan any token across 25+ chains for honeypots, rug pulls, bundles, "
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
    "🛡️ AI crypto intelligence — scan tokens, trace wallets, detect rugs before you buy. Free in beta."
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

# ══════════════════════════════════════════════
# BACKEND AUTH (X-API-Key)
# ══════════════════════════════════════════════
# The backend AuthMiddleware requires `X-API-Key` == RMI_AUTH_TOKEN on every
# non-public route (app/core/auth.py). The bot runs on the host (not in the
# backend container), so it reads the same secret file the backend container
# mounts as its Docker secret, falling back to the RMI_AUTH_TOKEN env var and
# then gopass. Sending the header ALSO bypasses the backend CSRF middleware
# (middleware_setup.py skips the check when X-API-Key is present).
_RMI_AUTH_SECRET_FILES = (
    "/srv/secrets/rmi_auth_token",  # Docker secret source — matches backend
    "/run/secrets/rmi_auth_token",  # in-container fallback (bot in Docker)
)


def _get_api_key() -> str:
    """Resolve the backend X-API-Key: secret file → env var → gopass."""
    for secret_file in _RMI_AUTH_SECRET_FILES:
        try:
            with open(secret_file) as f:
                key = f.read().strip()
            if key:
                return key
        except (FileNotFoundError, OSError):
            continue
    return _get_token("rmi/backend/rmi_auth_token", ["RMI_AUTH_TOKEN"])


API_KEY = _get_api_key()


def backend_headers() -> dict[str, str]:
    """Headers for bot→backend calls. X-API-Key authenticates AND bypasses CSRF."""
    return {"X-API-Key": API_KEY} if API_KEY else {}


async def check_backend_health() -> None:
    """Ping backend /health on startup. Logs WARNING if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BACKEND_URL}/health", headers=backend_headers())
            if r.status_code == 200:
                from app.domains.telegram.rugmunchbot.support.logging import get_logger

                get_logger("rmi.config").info("Backend reachable at %s", BACKEND_URL)
            else:
                from app.domains.telegram.rugmunchbot.support.logging import get_logger

                get_logger("rmi.config").warning("Backend at %s returned status %d", BACKEND_URL, r.status_code)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        from app.domains.telegram.rugmunchbot.support.logging import get_logger

        get_logger("rmi.config").warning("Backend UNREACHABLE at %s: %s", BACKEND_URL, exc)


# Alert engine
ALERT_CHECK_INTERVAL = 300  # seconds between alert checks (5 min)

# External APIs
DEXSCREENER = "https://api.dexscreener.com/latest/dex"
DEXSCREENER_TOKEN_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"  # noqa: S105 — config default
SOLANAFM_API = "https://api.solana.fm/v0"
COINGECKO = "https://api.coingecko.com/api/v3"  # price history & trending
GOPLUS = "https://api.gopluslabs.com/api/v1"  # EVM token security scanner
HONEYPOT = "https://api.honeypot.is/v2"  # primary EVM security scanner

# ══════════════════════════════════════════════
# SENTINEL DEEP FORENSIC SCAN
# ══════════════════════════════════════════════
# Full 9-module SENTINEL deep scan (POST /api/v1/x402-tools/sentinel_scan).
# Returns composite risk score (0-100), per-module breakdown, red flags, and
# aggregated findings. See app/domains/scanners/sentinel_pipeline/config.py
# (SentinelReport) for the response schema.
SENTINEL_URL = os.getenv("SENTINEL_URL", f"{BACKEND_URL}/api/v1/x402-tools/sentinel_scan")
SENTINEL_TIMEOUT = 60.0  # deep forensic scan can take a while (9+ modules)

# ══════════════════════════════════════════════
# SUBSCRIPTION TIERS (Weekly Allotments)
# ══════════════════════════════════════════════
# Weekly model: resets every Monday 00:00 UTC.
# Prevents day-one dump abuse and smooths API load.
# BETA MODE: all premium features unlocked for everyone during public beta.
# Set to False when billing goes live. This overrides tier checks in db.py.
BETA_MODE = os.environ.get("RMI_BOT_BETA_MODE", "true").lower() in ("true", "1", "yes")

TIERS: dict[str, TierDefinition] = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "scans_per_week": 25,
        # BETA BUMP: 15 → 50 AI msgs/week while rugmunchbot is in FREE
        # PUBLIC BETA (all features unlocked). Revert to 15 when billing
        # goes live — this one value drives db.check_rate_limit/increment.
        "ai_msgs_per_week": 50,
        "price_monthly": 0,
        "features": [
            "25 scans/week",
            "50 AI questions/week (beta boost)",
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
            "Holder analysis + concentration",
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
            "Cross-chain fund trace",
            "First buyer analysis",
            "Exchange flow tracking",
            "Fresh wallet detection",
            "Shill detection",
            "Social sentiment scan",
            "Watchlist (50 tokens)",
            "Real-time price alerts",
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
            "Deep investigation reports",
            "Smart money watch (live)",
            "Custom webhook integrations",
            "API access with tokens",
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
TOP_UP_PACKS: dict[str, TopUpPack] = {
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
BUY_STARS_PACKS: dict[str, StarsPack] = {
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
SCAN_PACKS: dict[str, ScanPack] = {
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
#
# LINK TAXONOMY (audited 2026-08-14 — every link must land users on the
# ACTUAL token's page, not a generic home page):
#   [chart]    token/chart page — `{token}` placeholder must be filled with
#              the scan address (GMGN / DexScreener / DexTools / Birdeye).
#   [swap]     token-specific swap page — acceptable, user lands on the exact
#              token pair (Jupiter / 1inch / PancakeSwap). Kept intentionally.
#   [signup]   Telegram bot signup — NO per-token URL exists; the ref code is
#              embedded in the `start=` payload (OdinBot / Banana Gun).
#   [home]     home page with ref param — NO verifiable per-token deep link
#              (Axiom / Padre). Kept + flagged [REVIEW]: if the platform adds
#              a token route (e.g. /trade/{addr}), switch the template to it.
# The `{token}` / `{chain}` / `{chain_id}` placeholders are filled by
# formatters/_base.py `_ref_platform_buttons` for every platform. Tests:
# tests/test_ref_links_chart.py locks in that the address always appears.
DEX_REF_LINKS = {
    "jupiter": {
        "name": "Jupiter",
        "emoji": "🪐",
        "url": "https://jup.ag/swap/SOL-{token}?ref=rugmunch",  # [swap] token pair
        "chains": ["solana"],
        "description": "Best Solana DEX aggregator",
    },
    "photon": {
        "name": "Photon",
        "emoji": "⚡",
        "url": "https://photon-sol.tinyastro.io/en/lp/{token}?handle=rugmunch",  # [chart] token terminal
        "chains": ["solana", "ethereum", "base"],
        "description": "Fast Solana trading terminal",
    },
    "gmgn": {
        "name": "GMGN.ai",
        "emoji": "📊",
        # [chart] token chart page. NOTE: `sol` slug is hardcoded — correct for
        # Solana (the dominant use case); EVM scans land on GMGN's /sol/ route
        # which resolves by address. Flagged [REVIEW] if cross-chain routing
        # must be explicit (then use gmgn_url() slug mapping in _base.py).
        "url": "https://gmgn.ai/sol/token/{token}?ref=rugmunch",
        "chains": [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "tron",
            "robinhood",
        ],
        "description": "Smart money tracking + trading",
    },
    "odinbot": {
        "name": "OdinBot",
        "emoji": "🧿",
        # [signup] Telegram bot — no per-token URL; ref rides in `start=`.
        "url": "https://t.me/OdinBot?start=x5pyi0",
        "chains": ["solana"],
        "description": "Solana trading bot",
    },
    "banana": {
        "name": "Banana Gun",
        "emoji": "🍌",
        # [signup] Telegram bot — no per-token URL; ref rides in `start=`.
        "url": "https://t.me/BananaGunBot?start=rugmunch",
        "chains": ["solana", "ethereum", "base"],
        "description": "Sniper + trading bot",
    },
    "axiom": {
        "name": "Axiom",
        "emoji": "🧮",
        # [home] no verifiable per-token deep link (SPA, no documented route).
        # Kept with ref — [REVIEW]: switch to a token page when one exists.
        "url": "https://axiom.trade/?ref=crmuncher",
        "chains": ["solana"],
        "description": "Smart-money trading terminal",
    },
    "padre": {
        "name": "Padre",
        "emoji": "⛪",
        # [home] redirects to axiom.trade/@handle — still a profile/home page,
        # no per-token deep link. Kept with ref — [REVIEW] if a token route
        # is added.
        "url": "https://padre.trade/?ref=crm",
        "chains": ["solana", "ethereum", "base"],
        "description": "DeFi trading bot",
    },
    "birdeye": {
        "name": "Birdeye",
        "emoji": "🦅",
        "url": "https://birdeye.so/token/{token}",  # [chart] token chart page (no ref program)
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Token analytics & charts",
    },
    "oneinch": {
        "name": "1inch",
        "emoji": "🦄",
        "url": "https://app.1inch.io/#/{chain_id}/simple/swap/ETH/{token}?ref=rugmunch",  # [swap] token pair
        "chains": ["ethereum", "base", "arbitrum", "bsc", "polygon", "avalanche"],
        "description": "Best EVM DEX aggregator",
    },
    "pancakeswap": {
        "name": "PancakeSwap",
        "emoji": "🥞",
        "url": "https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency={token}&ref=rugmunch",  # [swap] token pair
        "chains": ["bsc", "ethereum", "arbitrum", "base"],
        "description": "BSC's #1 DEX",
    },
}

# ══════════════════════════════════════════════
# JUPITER REFERRAL FEE ACCOUNTS (programmatic swap execution)
# ══════════════════════════════════════════════
# On-chain fee-recipient wallets for the Jupiter referral program. These are
# the PROGRAMMATIC counterpart to the ``?ref=rugmunch`` UI-attribution links
# in ``DEX_REF_LINKS["jupiter"]`` above (deep links only attribute the click;
# they never move funds). Any swap executed through Jupiter's program with
# ``feeAccount`` / ``platformFeeBps`` set routes a share of the swap fee to
# these wallets.
#
# Where they are used:
#   - walletpress plugin swap routing (walletpress repo) — the Solana swap
#     path sets ``feeAccount`` = ``JUPITER_FEE_ACCOUNT`` on the Jupiter v6
#     swap instruction so every executed swap pays the RugMunch referral fee.
#   - Future Jupiter Ultra / swap-execution paths in THIS repo must reuse
#     these constants instead of re-pasting the wallet strings.
JUPITER_FEE_ACCOUNT = "8ouwE3sx6im8pDVwKRZ6n2QNjZwLmrBQ1XTiHhsXge39"  # Jupiter v6 Swap fee recipient
JUPITER_ULTRA_ACCOUNT = "Dkrbs5cWeaVspAbCHUM4ds6kJdZeuJkgmaje9w9Urtfh"  # Jupiter Ultra fee recipient

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


def _risk_gauge(score: float) -> str:
    """Compact emoji risk gauge for scan results."""
    if score >= 80:
        return "🔴🔴🔴🔴🔴 EXTREME RISK"
    elif score >= 60:
        return "🟠🟠🟠🟠⬜ HIGH RISK"
    elif score >= 40:
        return "🟡🟡🟡⬜⬜ MODERATE"
    elif score >= 20:
        return "🟢🟢⬜⬜⬜ LOW RISK"
    else:
        return "✅✅✅✅✅ SAFE"


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
