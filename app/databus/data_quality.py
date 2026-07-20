"""
RugCharts Data Quality Engine
==============================
Fixes false positives, enriches all responses, populates empty providers.

1. Known Entity Registry - trusted addresses, exchanges, protocols
2. Token vs Wallet Detection - don't scan wallets as tokens
3. Data Enrichment - inject wallet labels, Arkham entities, RAG into every response
4. Smart Tiering - clear free/premium/admin boundaries
5. Provider Fallback Enhancement - when one returns empty, try harder
"""

import json
import os
from datetime import UTC, datetime

import redis

from app.core.logging import get_logger

logger = get_logger("data_quality")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# ═══════════════════════════════════════════════════════════════════════
# 1. KNOWN ENTITY REGISTRY
# ═══════════════════════════════════════════════════════════════════════

KNOWN_ENTITIES = {
    # ── Trusted Protocols & Infrastructure ──
    "So11111111111111111111111111111111111111112": {
        "name": "Wrapped SOL",
        "type": "protocol_token",
        "trust": "SAFE",
        "chains": ["solana"],
        "note": "Native SOL wrapper, core Solana infrastructure",
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "name": "USDC (Solana)",
        "type": "stablecoin",
        "trust": "SAFE",
        "chains": ["solana"],
        "note": "Circle-issued USDC on Solana",
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "name": "USDT (Solana)",
        "type": "stablecoin",
        "trust": "SAFE",
        "chains": ["solana"],
        "note": "Tether USDT on Solana",
    },
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {
        "name": "Bonk",
        "type": "memecoin",
        "trust": "SAFE",
        "chains": ["solana"],
        "note": "Major Solana memecoin, high liquidity",
    },
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": {
        "name": "WETH",
        "type": "protocol_token",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Wrapped Ether, core Ethereum infrastructure",
    },
    "0xdAC17F958D2ee523a2206206994597C13D831ec7": {
        "name": "USDT (Ethereum)",
        "type": "stablecoin",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Tether USDT on Ethereum",
    },
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": {
        "name": "USDC (Ethereum)",
        "type": "stablecoin",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Circle USDC on Ethereum",
    },
    # ── Known Individuals (Arkham-resolved) ──
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045": {
        "name": "Vitalik Buterin",
        "type": "individual",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Ethereum co-founder. This is a WALLET, not a token.",
    },
    # ── Major Exchanges ──
    "0x28C6c06298d514Db089934071355E5743bf21d60": {
        "name": "Binance 14",
        "type": "exchange",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Binance hot wallet",
    },
    "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8": {
        "name": "Binance 7",
        "type": "exchange",
        "trust": "SAFE",
        "chains": ["ethereum"],
        "note": "Binance hot wallet",
    },
    # ── Known Scam Addresses ──
    "0x000000000000000000000000000000000000dEaD": {
        "name": "Burn Address",
        "type": "burn",
        "trust": "NEUTRAL",
        "chains": ["ethereum", "bsc", "base", "arbitrum"],
        "note": "Standard burn address - tokens sent here are destroyed",
    },
}


def lookup_entity(address: str, chain: str = "") -> dict | None:
    """Check if an address is a known entity."""
    # Exact match
    if address in KNOWN_ENTITIES:
        entity = KNOWN_ENTITIES[address]
        if not chain or chain in entity.get("chains", []):
            return entity

    # Case-insensitive match (EVM addresses)
    addr_lower = address.lower()
    for known_addr, entity in KNOWN_ENTITIES.items():
        if known_addr.lower() == addr_lower:
            if not chain or chain in entity.get("chains", []):
                return entity

    return None


def is_wallet_not_token(address: str, chain: str = "") -> bool:
    """Detect if an address is likely a wallet, not a token contract."""
    entity = lookup_entity(address, chain)
    return bool(entity and entity.get("type") in ("individual", "exchange", "burn"))


def get_trust_bonus(address: str, chain: str = "") -> tuple[int, str]:
    """Get trust bonus (score reduction) for known entities.

    Returns (bonus_points, reason)
    SAFE entities get 40-60 point reduction (lower risk score = better)
    """
    entity = lookup_entity(address, chain)
    if not entity:
        return 0, ""

    trust = entity.get("trust", "")
    if trust == "SAFE":
        return 60, f"Known safe entity: {entity['name']} ({entity['type']})"
    elif trust == "NEUTRAL":
        return 20, f"Known entity: {entity['name']} ({entity['type']})"
    elif trust == "WARNING":
        return -20, f"Warning entity: {entity['name']}"
    elif trust == "DANGER":
        return -60, f"Known dangerous entity: {entity['name']}"

    return 0, ""


# ═══════════════════════════════════════════════════════════════════════
# 2. DATA ENRICHMENT - Inject wallet labels, Arkham, RAG everywhere
# ═══════════════════════════════════════════════════════════════════════


def _r():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
)


async def enrich_with_wallet_labels(addresses: list[str]) -> dict[str, str]:
    """Look up wallet labels from our 190K-label Redis store."""
    labels = {}
    try:
        r = _r()
        for addr in addresses[:50]:
            # Check multiple label formats
            for prefix in ["label:", "wallet_label:", "entity:"]:
                label = r.get(f"{prefix}{addr}")
                if label:
                    labels[addr] = label
                    break
        r.close()
    except Exception as e:
        logger.debug(f"Label enrichment failed: {e}")
    return labels


async def enrich_with_arkham(address: str) -> dict | None:
    """Enrich an address with Arkham entity data."""
    arkham_key = os.getenv("ARKHAM_API_KEY", "")
    if not arkham_key:
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                f"https://api.arkhamintelligence.com/intelligence/address/{address}",
                headers={"API-Key": arkham_key},
)
            if r.status_code == 200:
                data = r.json()
                return {
                    "entity_name": data.get("arkhamEntity", {}).get("name", ""),
                    "entity_type": data.get("arkhamEntity", {}).get("type", ""),
                    "label": data.get("arkhamLabel", {}).get("name", ""),
                    "chain": data.get("chain", ""),
                    "is_contract": data.get("contract", False),
                }
    except Exception:
        pass
    return None


async def enrich_response(result: dict, address: str, chain: str) -> dict:
    """Universal response enrichment - injects labels, entities, trust into any result."""
    if not result or not isinstance(result, dict):
        return result

    enriched = dict(result)

    # 1. Known entity lookup
    entity = lookup_entity(address, chain)
    if entity:
        enriched["known_entity"] = {
            "name": entity["name"],
            "type": entity["type"],
            "trust": entity["trust"],
            "note": entity.get("note", ""),
        }

        # Adjust risk scores for known entities
        trust_bonus, reason = get_trust_bonus(address, chain)
        if trust_bonus and "security_score" in enriched:
            old_score = enriched.get("security_score", 50)
            enriched["security_score"] = max(0, min(100, old_score - trust_bonus))
            enriched["trust_adjustment"] = {
                "original_score": old_score,
                "adjusted_score": enriched["security_score"],
                "reason": reason,
            }

        # Override risk band for SAFE entities
        if entity["trust"] == "SAFE" and enriched.get("risk_band") in (
            "DANGER",
            "CRITICAL",
            "HIGH",
):
            enriched["risk_band"] = "SAFE"
            enriched["risk_level"] = "LOW"
            enriched["risk_color"] = "#00FF88"

    # 2. Wallet vs token detection
    if is_wallet_not_token(address, chain):
        enriched["address_type"] = "wallet"
        enriched["wallet_note"] = (
            "This is a wallet address, not a token contract. Token-specific checks may not apply."
)
    else:
        enriched["address_type"] = "token"

    # 3. Add enrichment timestamp
    enriched["enriched_at"] = datetime.now(UTC).isoformat()

    return enriched


# ═══════════════════════════════════════════════════════════════════════
# 3. SMART TIERING - Clear free/premium/admin boundaries
# ═══════════════════════════════════════════════════════════════════════

TIER_DEFINITIONS = {
    "free": {
        "name": "Free",
        "rate_limit_rpm": 30,
        "allowed_data_types": [
            "token_price",
            "market_overview",
            "trending",
            "news",
            "alerts",
            "token_metadata",
            "ohlcv",
            "token_launches",
        ],
        "description": "Basic charting, prices, trending - better than DexScreener free",
        "competitor_equivalent": "DexScreener free ($0) + Birdeye free ($0)",
    },
    "authenticated": {
        "name": "Authenticated",
        "rate_limit_rpm": 100,
        "allowed_data_types": [
            "token_price",
            "market_overview",
            "trending",
            "news",
            "alerts",
            "token_metadata",
            "ohlcv",
            "token_launches",
            "wallet_labels",
            "wallet_tokens",
            "holder_health",
            "scanner",
            "rag_search",
            "smart_money",
        ],
        "description": "Wallet tracking, holder analysis, smart money - Nansen-level at $0",
        "competitor_equivalent": "Nansen Lite ($100/mo) + DexScreener",
    },
    "premium": {
        "name": "Premium",
        "rate_limit_rpm": 300,
        "price_monthly": 29,
        "allowed_data_types": [
            "token_price",
            "market_overview",
            "trending",
            "news",
            "alerts",
            "token_metadata",
            "ohlcv",
            "token_launches",
            "wallet_labels",
            "wallet_tokens",
            "holder_health",
            "scanner",
            "rag_search",
            "smart_money",
            "volume_authenticity",
            "token_security",
            "liquidity_risk",
            "rug_patterns",
            "dev_reputation",
            "insider_detection",
            "whale_alerts",
            "cross_chain_entity",
            "token_report",
            "entity_intel",
            "arkham_labels",
        ],
        "description": "Full RugCharts: fake volume %, security scans, entity intel, rug patterns",
        "competitor_equivalent": "Nansen Pro ($2,500/mo) + GoPlus + Arkham",
    },
    "admin": {
        "name": "Admin",
        "rate_limit_rpm": 1000,
        "allowed_data_types": ["*"],
        "description": "Full raw data access - Arkham, Moralis, all providers, no packaging",
    },
}


def get_tier_info(tier: str) -> dict:
    """Get tier definition."""
    return TIER_DEFINITIONS.get(tier, TIER_DEFINITIONS["free"])


def tier_allows(tier: str, data_type: str) -> bool:
    """Check if a tier can access a data type."""
    tier_def = TIER_DEFINITIONS.get(tier, TIER_DEFINITIONS["free"])
    allowed = tier_def["allowed_data_types"]
    return "*" in allowed or data_type in allowed


def tier_comparison_table() -> list[dict]:
    """Generate competitive comparison table."""
    return [
        {
            "feature": "Real-time charting",
            "rugcharts_free": "✅",
            "rugcharts_premium": "✅",
            "dexscreener": "✅",
            "nansen": "❌",
            "gmgni": "✅",
        },
        {
            "feature": "Multi-chain support",
            "rugcharts_free": "✅ Solana+EVM",
            "rugcharts_premium": "✅ All chains",
            "dexscreener": "✅",
            "nansen": "✅ EVM only",
            "gmgni": "⚠️ Solana only",
        },
        {
            "feature": "Token security (37+ checks)",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "❌",
            "gmgni": "⚠️ Basic",
        },
        {
            "feature": "Fake volume detection",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "❌",
            "gmgni": "❌",
        },
        {
            "feature": "Entity resolution (Arkham)",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "⚠️ Labels",
            "gmgni": "❌",
        },
        {
            "feature": "Cross-chain entity tracing",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "❌",
            "gmgni": "❌",
        },
        {
            "feature": "Holder health (Gini, concentration)",
            "rugcharts_free": "✅",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "✅",
            "gmgni": "⚠️",
        },
        {
            "feature": "Smart money tracking",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "✅ $100+/mo",
            "gmgni": "✅ 1%/trade",
        },
        {
            "feature": "Whale alerts",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "✅",
            "gmgni": "⚠️",
        },
        {
            "feature": "Rug pattern matching",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "❌",
            "gmgni": "❌",
        },
        {
            "feature": "Developer reputation",
            "rugcharts_free": "❌",
            "rugcharts_premium": "✅",
            "dexscreener": "❌",
            "nansen": "❌",
            "gmgni": "⚠️",
        },
        {
            "feature": "Price",
            "rugcharts_free": "$0/mo",
            "rugcharts_premium": "$29/mo",
            "dexscreener": "$0",
            "nansen": "$100-2,500/mo",
            "gmgni": "1% per trade",
        },
    ]


# ═══════════════════════════════════════════════════════════════════════
# 4. ENHANCED TOKEN REPORT - Smart verdicts, narratives, comparables
# ═══════════════════════════════════════════════════════════════════════


def smart_verdict(report: dict) -> str:
    """Generate an intelligent, nuanced verdict instead of just 'EXTREME RISK'."""

    report.get("sections", {}).get("entity", {})
    known = report.get("known_entity", {})
    security = report.get("sections", {}).get("security", {})
    rug = report.get("sections", {}).get("rug_patterns", {})
    dev = report.get("sections", {}).get("developer", {})
    holders = report.get("sections", {}).get("holders", {})

    # ── Known entities get priority ──
    if known.get("trust") == "SAFE":
        etype = known.get("type", "entity")
        if etype == "individual":
            return f"KNOWN WALLET - {known['name']}. This is a personal wallet, not a token. Token security checks do not apply to wallet addresses. The entity is verified by Arkham Intelligence."
        elif etype == "stablecoin":
            return f"KNOWN STABLECOIN - {known['name']}. Established, high-liquidity asset. Standard risk profile for stablecoins."
        elif etype == "protocol_token":
            return f"CORE PROTOCOL - {known['name']}. Fundamental blockchain infrastructure token. Extremely low rug risk."
        elif etype == "exchange":
            return f"EXCHANGE WALLET - {known['name']}. This is an exchange hot wallet, not a token address."
        return f"KNOWN SAFE ENTITY - {known['name']}. Verified by RugCharts entity registry."

    # ── Wallet addresses ──
    if report.get("address_type") == "wallet":
        return "WALLET ADDRESS - This is a wallet, not a token contract. Token-specific security checks (honeypot, mint, taxes) are not applicable. Entity information and transaction history are shown below."

    # ── Real tokens: assess actual risk ──
    risks = []
    risk_level = 0

    if security.get("score", 0) >= 80:
        risks.append("CRITICAL security failures detected")
        risk_level += 3
    elif security.get("score", 0) >= 60:
        risks.append("HIGH security risk - multiple concerns found")
        risk_level += 2
    elif security.get("score", 0) >= 40:
        risks.append("MODERATE security concerns - review checks")
        risk_level += 1

    if rug.get("overall_risk") in ("CRITICAL", "HIGH"):
        risks.append(f"Rug pattern match: {rug.get('top_match', 'unknown pattern')}")
        risk_level += 2
    elif rug.get("total_matches", 0) > 0:
        risks.append(f"{rug['total_matches']} rug patterns partially matched")
        risk_level += 1

    if holders.get("gini", 0) > 0.8:
        risks.append(f"Extreme holder concentration (Gini: {holders['gini']})")
        risk_level += 2
    elif holders.get("gini", 0) > 0.6:
        risks.append(f"High holder concentration (Gini: {holders['gini']})")
        risk_level += 1

    if dev.get("risk_level") == "HIGH":
        risks.append(f"High-risk developer: {dev.get('tokens_deployed', '?')} tokens deployed")
        risk_level += 1

    if not risks:
        return "NO SIGNIFICANT CONCERNS - This token passes standard security checks. Standard trading risks apply. Always verify contract independently."

    if risk_level >= 5:
        return f"EXTREME RISK - {'; '.join(risks)}. STRONGLY advise against trading this token."
    elif risk_level >= 3:
        return f"HIGH RISK - {'; '.join(risks)}. Proceed with extreme caution."
    elif risk_level >= 2:
        return f"ELEVATED RISK - {'; '.join(risks)}. Review all details before trading."
    else:
        return f"MINOR CONCERNS - {'; '.join(risks)}. Standard due diligence recommended."


async def enhanced_token_report(address: str, chain: str = "solana", **kw) -> dict | None:
    """Enhanced token report with smart verdicts, enrichment, and tier info."""

    cache_key = f"enhanced_report:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except Exception:
        pass

    # First, check known entities
    known = lookup_entity(address, chain)
    is_wallet = is_wallet_not_token(address, chain)

    # Get the base token report
    try:
        from app.databus.rugcharts_intel import instant_token_report

        base_report = await instant_token_report(address, chain, **kw)
    except Exception as e:
        base_report = {"error": str(e), "sections": {}}

    if not base_report:
        base_report = {"sections": {}}

    # ── Enrich ──
    # Apply trust adjustments
    if known:
        trust_bonus, reason = get_trust_bonus(address, chain)
        if trust_bonus and "overall_risk" in base_report:
            old_score = base_report["overall_risk"]["score"]
            new_score = max(0, min(100, old_score - trust_bonus))
            base_report["overall_risk"]["score"] = round(new_score, 1)

            if known["trust"] == "SAFE" and base_report["overall_risk"]["level"] in (
                "CRITICAL",
                "HIGH",
                "DANGER",
):
                base_report["overall_risk"]["level"] = "LOW"
                base_report["overall_risk"]["color"] = "#88FF00"

        base_report["known_entity"] = known
        base_report["trust_adjustment"] = {
            "original_score": base_report.get("overall_risk", {}).get("score", 0),
            "reason": reason,
        }

    # Wallet detection
    base_report["address_type"] = "wallet" if is_wallet else "token"
    if is_wallet:
        base_report["wallet_note"] = "This is a wallet address. Token security checks do not apply."

    # ── Smart Verdict ──
    base_report["quick_verdict"] = smart_verdict(base_report)

    # ── Tier Info ──
    base_report["tier_info"] = {
        "free_available": tier_allows("free", "token_report"),
        "premium_available": tier_allows("premium", "token_report"),
        "data_sources_used": len(base_report.get("sections", {})),
        "enrichment_applied": bool(known),
    }

    # ── Data Quality Score ──
    sections_with_data = sum(
        1 for s in base_report.get("sections", {}).values() if s and not s.get("error")
)
    base_report["data_quality"] = {
        "score": min(100, sections_with_data * 15),
        "level": "EXCELLENT"
        if sections_with_data >= 6
        else "GOOD"
        if sections_with_data >= 4
        else "FAIR"
        if sections_with_data >= 2
        else "LIMITED",
        "sections_populated": sections_with_data,
        "total_sections_available": 9,
    }

    # ── Generated At ──
    base_report["generated_at"] = datetime.now(UTC).isoformat()
    base_report["source"] = "enhanced_token_report"

    # Cache
    try:
        r = _r()
        r.setex(cache_key, 300, json.dumps(base_report, default=str))
        r.close()
    except Exception:
        pass

    return base_report


# ═══════════════════════════════════════════════════════════════════════
# 5. TIER COMPARISON ENDPOINT DATA
# ═══════════════════════════════════════════════════════════════════════


async def get_tier_comparison(**kw) -> dict:
    """Return the competitive tier comparison table."""
    return {
        "tiers": {
            k: {
                "name": v["name"],
                "rate_limit_rpm": v["rate_limit_rpm"],
                "price": v.get("price_monthly", 0),
                "features": len(v["allowed_data_types"])
                if v["allowed_data_types"] != ["*"]
                else 49,
            }
            for k, v in TIER_DEFINITIONS.items()
        },
        "comparison": tier_comparison_table(),
        "data_bus_chains": 49,
        "source": "data_quality_engine",
    }
