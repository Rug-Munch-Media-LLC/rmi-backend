# ═══════════════════════════════════════════
# 4-TIER PRICING — Competitive with market
# ═══════════════════════════════════════════
# CoinGecko API: Free 30/min, Pro $129/mo
# CoinMarketCap: Free 10K/mo, Pro $79/mo
# Moralis: Free 40K/day, Pro $49/mo
# Alchemy: Free 300M CU/mo, Growth $49/mo
# RMI: Free generous, Pro $25 undercuts everyone, Elite $69 with AI, Power $149 white-label

import time
from enum import StrEnum

from fastapi import APIRouter, Header, HTTPException

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


class Tier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ELITE = "elite"
    POWER = "power"


TIERS = {
    Tier.FREE: {
        "price_usd": 0,
        "req_per_day": 100,
        "req_per_min": 10,
        "sentinel_scans_day": 5,
        "ai_calls_day": 20,
        "features": ["basic_scan", "market_data", "fear_greed", "trending"],
    },
    Tier.PRO: {
        "price_usd": 25,
        "req_per_day": 10000,
        "req_per_min": 60,
        "sentinel_scans_day": 100,
        "ai_calls_day": 500,
        "features": ["deep_scan", "token_report", "whale_alerts", "ai_chat", "api_access", "email_alerts", "sentiment"],
    },
    Tier.ELITE: {
        "price_usd": 69,
        "req_per_day": 50000,
        "req_per_min": 200,
        "sentinel_scans_day": 500,
        "ai_calls_day": 2000,
        "features": [
            "all_pro_features",
            "insider_detection",
            "arbitrage_scanner",
            "mev_advisory",
            "prediction_markets",
            "signal_generator",
            "ai_streaming",
            "vision_analysis",
            "nft_detector",
            "chain_compare",
        ],
    },
    Tier.POWER: {
        "price_usd": 149,
        "req_per_day": 200000,
        "req_per_min": 500,
        "sentinel_scans_day": 2000,
        "ai_calls_day": 10000,
        "features": [
            "all_features",
            "white_label",
            "custom_models",
            "dedicated_support",
            "sla_guarantee",
            "on_prem_deploy",
            "api_key_management",
            "team_accounts",
            "audit_logs",
        ],
    },
    "enterprise": {
        "price_usd": "Custom",
        "req_per_day": "Unlimited",
        "req_per_min": "Unlimited",
        "sentinel_scans_day": "Unlimited",
        "ai_calls_day": "Unlimited",
        "features": [
            "all_power_features",
            "unlimited_everything",
            "custom_sla",
            "dedicated_infrastructure",
            "on_prem_deploy",
            "source_code_access",
            "priority_support_24_7",
        ],
        "contact": "biz@rugmunch.io",
        "note": "Contact us for enterprise pricing.",
    },
}

# Payment options — 16 chains, crypto only
PAYMENTS = {
    "solana": [
        {"token": "SOL", "wallet": "G1uZFfsdxK2PuH2Em5LhRkEcptxHWe4vy5BZak67QyKv"},
        {"token": "USDC", "wallet": "G1uZFfsdxK2PuH2Em5LhRkEcptxHWe4vy5BZak67QyKv"},
    ],
    "ethereum": [
        {"token": "ETH", "wallet": "0x1E3AC01d0fdb976179790BDD02823196A92705C9"},
        {"token": "USDC", "wallet": "0x1E3AC01d0fdb976179790BDD02823196A92705C9"},
        {"token": "USDT", "wallet": "0x1E3AC01d0fdb976179790BDD02823196A92705C9"},
    ],
    "bitcoin": [{"token": "BTC", "wallet": "bc1qzkllckr024wqttm4px6egcwsya9tcrgghm46kq"}],
    "tron": [
        {"token": "TRX", "wallet": "TPskriENxSyXGC39waUwDwCZebeE5QVnLh"},
        {"token": "USDT", "wallet": "TPskriENxSyXGC39waUwDwCZebeE5QVnLh"},
    ],
    "xrp": [{"token": "XRP", "wallet": "rG62QiXrbqbsSUimUyxLCLRybLmu6ykUH9"}],
    "ton": [{"token": "TON", "wallet": "UQDYJBI8NA1waorcZG_cOnLik73ZlQbFhNST1_nPzgynWzvB"}],
    "stellar": [{"token": "XLM", "wallet": "GB2KF5GCT4ZBYNRTMOYBJXJO7JKAABBVX6VTNRO6P5KGYIP5CG5QUI24"}],
    "sui": [{"token": "SUI", "wallet": "0xf51639da22d7427afeb7bd152002187346a634e4487e3ddd0e637804a47c6aad"}],
    "monad": [{"token": "MONAD", "wallet": "0x01ffa655FBc2aE5433A21fa7aB2e5aB158008C84"}],
    "litecoin": [{"token": "LTC", "wallet": "ltc1qtuvtp5njgxpag87kdefp3agst6jtfr2vauejtv"}],
    "dogecoin": [{"token": "DOGE", "wallet": "DDoHrBgHwfQojTYYMdsDUVVaBuE6bvD1WQ"}],
    "cardano": [
        {
            "token": "ADA",
            "wallet": "addr1q8hh8ptw7qvp4j84jredg66vah0d9pyyt4fzl8kcu94sxyxdg7z7sdg27448vv4d0wehwhycxmwfuqwkr8y4a5lls24sdl66wg",
        }
    ],
    "zcash": [{"token": "ZEC", "wallet": "t1YDeXgob88VsDJn1Qn2dTWNHdMCtvXa6Ko"}],
    "polkadot": [{"token": "DOT", "wallet": "14cWAqWmTCQqTCEG6FySdnCayQbGU8M4gP9vsmm7W8A8KB5Z"}],
    "near": [{"token": "NEAR", "wallet": "d605f4401464aaa0b21bf35cfb1d28c1743b7ade39e459f432084860afda8ab4"}],
    "aptos": [{"token": "APT", "wallet": "0xe8d2b815aea16fdf1e11b41e974818c454313403c57948c35f8928dda20b304"}],
    "x402": [{"note": "Pay per API call. No subscription. $0.01-$0.25/call."}],
}

_inmem_users: dict[str, dict] = {}


def get_user_tier(user_id: str) -> Tier:
    if user_id in _inmem_users:
        return Tier(_inmem_users[user_id]["tier"])
    return Tier.FREE


@router.get("/tiers")
async def get_tiers():
    return {
        "tiers": {t.value: v for t, v in TIERS.items() if isinstance(t, Tier)},
        "enterprise": TIERS["enterprise"],
        "payments": PAYMENTS,
    }


@router.get("/me")
async def my_tier(x_api_key: str = Header(None)):
    uid = x_api_key or "anonymous"
    tier = get_user_tier(uid)
    return {"user": uid, "tier": tier.value, "limits": TIERS[tier], "upgrade": "/api/v1/billing/upgrade"}


@router.post("/upgrade/{tier}")
async def upgrade(tier: str, user_id: str, tx_sig: str = ""):
    if tier not in [t.value for t in Tier]:
        raise HTTPException(400, "Invalid tier")
    _inmem_users[user_id] = {"tier": tier, "tx": tx_sig, "upgraded_at": time.time()}
    return {"status": "upgraded", "tier": tier, "price": TIERS[Tier(tier)]["price_usd"]}
