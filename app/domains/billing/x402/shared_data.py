"""x402 shared data — constants, pricing, token tables, tool aliases."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("x402_tools")

# Caching shield - all data calls route through cache → rate limit → provider chain
from app.caching_shield.service_mcp import (  # noqa: E402 — module docstring precedes import block
    get_service_mcp,
)

_svc_mcp = get_service_mcp()

# ── Data Sources (multi-layer fallback) ─────────────────────────

# Free public RPCs for blockchain queries
FREE_RPCS: dict[str, list[str]] = {
    "solana": [
        "https://api.mainnet-beta.solana.com",
        "https://solana.publicnode.com",
        "https://api.devnet.solana.com",
    ],
    "base": [
        "https://base.llamarpc.com",
        "https://base.publicnode.com",
        "https://developer-access-mainnet.base.org",
    ],
    "ethereum": [
        "https://eth.llamarpc.com",
        "https://ethereum.publicnode.com",
        "https://rpc.ankr.com/eth",
    ],
    "bsc": [
        "https://bsc-dataseed.binance.org",
        "https://bsc.publicnode.com",
    ],
}

# Free API endpoints (no key required)
FREE_APIS: dict[str, str] = {
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "coingecko": "https://api.coingecko.com/api/v3",
    "birdeye_public": "https://public-api.birdeye.so",
    "jupiter": "https://api.jup.ag",
    "defillama": "https://api.llama.fi",
    "pumpfun": "https://frontend-api.pump.fun",
}


BUNDLES: dict[str, dict[str, Any]] = {
    "security_pack": {
        "name": "Security Pack",
        "description": "Complete pre-trade security check - rug scan, contract audit, URL safety, and honeypot detection.",
        "tools": ["rugshield", "audit", "urlcheck", "honeypot_check"],
        "individual_total": 0.13,  # 0.02 + 0.05 + 0.01 + 0.05
        "bundle_price_usd": 0.10,  # 23% discount
        "bundle_price_atoms": "100000",
        "category": "bundle",
        "trial_free": 1,
    },
    "intelligence_pack": {
        "name": "Intelligence Pack",
        "description": "Whale tracking suite - decode wallets, follow smart money, detect clusters and insider patterns.",
        "tools": ["whale", "smartmoney", "cluster", "insider"],
        "individual_total": 0.35,  # 0.15 + 0.05 + 0.05 + 0.10
        "bundle_price_usd": 0.25,  # 29% discount
        "bundle_price_atoms": "250000",
        "category": "bundle",
        "trial_free": 1,
    },
    "all_in_one": {
        "name": "All-in-One Audit",
        "description": "Maximum intelligence - comprehensive audit + smart money alpha + meme vibe score in one call.",
        "tools": ["comprehensive_audit", "smart_money_alpha", "meme_vibe_score"],
        "individual_total": 0.50,  # 0.15 + 0.25 + 0.10
        "bundle_price_usd": 0.35,  # 30% discount
        "bundle_price_atoms": "350000",
        "category": "bundle",
        "trial_free": 1,
    },
    "forensic_pack": {
        "name": "Forensic Investigation Pack",
        "description": "Complete forensic analysis - valuation, OSINT identity hunt, and investigation report at 33% discount.",
        "tools": ["forensic_valuation", "osint_identity_hunt", "investigation_report"],
        "individual_total": 0.60,  # 0.25 + 0.15 + 0.20
        "bundle_price_usd": 0.40,  # 33% discount
        "bundle_price_atoms": "400000",
        "category": "bundle",
        "trial_free": 1,
    },
}


TOOL_ALIASES: dict[str, str] = {
    # Security
    "airdrop_check": "airdrop_finder",
    "bundler_detect": "mev_protection",
    "clone_detect": "contract_diff",
    "deployer_history": "insider",
    "fresh_pair": "launch",
    "liquidity_migration": "rugshield",
    "mev_alert": "mev_protection",
    "profile_flip": "social_signal",
    "protocol_risk": "chain_health",
    "scam_database": "urlcheck",
    "token_age": "audit",
    "wash_trading": "nft_wash_detector",
    # Intelligence
    "alpha_digest": "smart_money_alpha",
    "insider_network": "insider",
    "kol_performance": "social_signal",
    "listing_predictor": "market_overview",
    "sniper_detect": "sniper_alert",
    "syndicate_scan": "cluster",
    "syndicate_track": "cluster",
    "whale_accumulation": "whale",
    "whale_profile": "whale",
    "whale_scan": "whale",
    "wallet_graph": "cluster",
    # Market
    "arbitrage_scan": "market_overview",
    "liquidity_depth": "market_overview",
    "unlock_calendar": "market_overview",
    # Social
    "sentiment_spike": "sentiment",
    # Analysis
    "portfolio_aggregate": "portfolio_tracker",
    "wallet_pnl": "wallet",
    # Premium (mapped to closest real tool)
    "forensic_valuation": "forensics",
    "investigation_report": "forensics",
    "osint_identity_hunt": "social_signal",
    # Launchpad
    # (fresh_pair already mapped to launch above)
}

# ── Expanded tool aliases (44 new specialized tools → real handlers) ──
try:
    from app.routers._expanded_aliases import EXPANDED_ALIASES

    TOOL_ALIASES.update(EXPANDED_ALIASES)
except (AttributeError, ImportError):
    pass


# Supported payment tokens across all chains
HUMAN_PAYMENT_TOKENS: dict[str, dict[str, Any]] = {
    # Base chain
    "USDC-BASE": {
        "chain": "base",
        "network": "eip155:8453",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Base",
        "icon": "💵",
    },
    # Solana chain
    "USDC-SOL": {
        "chain": "solana",
        "network": "solana:mainnet",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Solana",
        "icon": "💵",
    },
    "SOL": {
        "chain": "solana",
        "network": "solana:mainnet",
        "asset": "SOL",
        "decimals": 9,
        "label": "SOL native",
        "icon": "◎",
    },
    # Ethereum chain
    "USDC-ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Ethereum",
        "icon": "💵",
    },
    "USDT-ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "USDT",
        "decimals": 6,
        "label": "USDT on Ethereum",
        "icon": "💲",
    },
    "ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "ETH",
        "decimals": 18,
        "label": "ETH native",
        "icon": "⟠",
    },
    # BNB Chain
    "USDC-BSC": {
        "chain": "bsc",
        "network": "eip155:56",
        "asset": "USDC",
        "decimals": 18,
        "label": "USDC on BSC",
        "icon": "💵",
    },
    "USDT-BSC": {
        "chain": "bsc",
        "network": "eip155:56",
        "asset": "USDT",
        "decimals": 18,
        "label": "USDT on BSC",
        "icon": "💲",
    },
    # Polygon
    "USDC-POLY": {
        "chain": "polygon",
        "network": "eip155:137",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Polygon",
        "icon": "💵",
    },
    "POL": {
        "chain": "polygon",
        "network": "eip155:137",
        "asset": "POL",
        "decimals": 18,
        "label": "POL native",
        "icon": "🟣",
    },
    # Arbitrum
    "USDC-ARB": {
        "chain": "arbitrum",
        "network": "eip155:42161",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Arbitrum",
        "icon": "💵",
    },
    # TRON
    "USDT-TRON": {
        "chain": "tron",
        "network": "tron:mainnet",
        "asset": "USDT",
        "decimals": 6,
        "label": "USDT on TRON",
        "icon": "💲",
    },
    "USDC-TRON": {
        "chain": "tron",
        "network": "tron:mainnet",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on TRON",
        "icon": "💵",
    },
    # Bitcoin
    "BTC": {
        "chain": "bitcoin",
        "network": "bitcoin:mainnet",
        "asset": "BTC",
        "decimals": 8,
        "label": "Bitcoin",
        "icon": "₿",
    },
    # Fiat
    "EUR-SEPA": {
        "chain": "sepa",
        "network": "sepa:eur",
        "asset": "EUR",
        "decimals": 2,
        "label": "EUR via SEPA",
        "icon": "€",
    },
}


# Pay-to addresses - pulled dynamically from WalletManagerV2
# Falls back to env vars if wallet manager unavailable
def _resolve_pay_to(chain: str) -> str:
    """Get active x402 payment address from WalletManagerV2."""
    try:
        from app.wallet_manager_v2 import get_wallet_manager_v2

        mgr = get_wallet_manager_v2(password=os.getenv("WALLET_VAULT_PASSWORD", ""))
        # Map chain key to wallet manager chain key
        chain_map = {
            "base": "eth",
            "ethereum": "eth",
            "bsc": "eth",
            "polygon": "eth",
            "arbitrum": "eth",
            "optimism": "eth",
            "avalanche": "eth",
            "fantom": "eth",
            "gnosis": "eth",
            "solana": "sol",
            "tron": "trx",
            "bitcoin": "btc",
        }
        wm_chain = chain_map.get(chain, chain)
        for w in mgr._wallets.values():
            if w.chain == wm_chain and w.x402_enabled and w.status == "active":
                return w.address
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass
    # Fallback: env vars
    fallbacks = {
        "base": "X402_EVM_PAY_TO",
        "ethereum": "X402_EVM_PAY_TO",
        "bsc": "X402_EVM_PAY_TO",
        "polygon": "X402_EVM_PAY_TO",
        "arbitrum": "X402_EVM_PAY_TO",
        "solana": "X402_SOL_PAY_TO",
        "tron": "X402_TRON_PAY_TO",
        "bitcoin": "X402_BTC_PAY_TO",
        "sepa": "ASTERPAY_SEPA_IBAN",
    }
    env_key = fallbacks.get(chain)
    if env_key:
        return os.getenv(env_key, "")
    return ""


# Pay-to addresses per chain — resolved at import time from WalletManagerV2
# with env-var fallback. Kept as a module-level dict so callers (shared.py
# re-export, x402_tools.py shim, integration/payments.py) can snapshot the
# active payment addresses without re-resolving on every request.
HUMAN_PAY_TO: dict[str, str] = {
    "base": _resolve_pay_to("base"),
    "ethereum": _resolve_pay_to("ethereum"),
    "bsc": _resolve_pay_to("bsc"),
    "polygon": _resolve_pay_to("polygon"),
    "arbitrum": _resolve_pay_to("arbitrum"),
    "solana": _resolve_pay_to("solana"),
    "tron": _resolve_pay_to("tron"),
    "bitcoin": _resolve_pay_to("bitcoin"),
    "sepa": os.getenv("ASTERPAY_SEPA_IBAN", ""),
}
