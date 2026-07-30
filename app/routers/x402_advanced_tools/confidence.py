"""
Layer 2: Confidence Scores — add low/medium/high confidence to every tool result.

Evaluates source quality (high/medium quality sources), source count,
and flags such as freshness indicators.
"""

from __future__ import annotations

logger = __import__("logging").getLogger("x402.confidence")


def compute_confidence(result: dict) -> dict:
    """Add confidence scoring to any tool result."""
    sources = result.get("sources_used", [])
    source_count = len(sources)

    # Source quality weights
    high_quality = {
        "clickhouse",
        "wallet_labels",
        "etherscan",
        "coingecko",
        "defillama",
        "solana_rpc",
        "ethereum_rpc",
        "base_rpc",
        "dexscreener",
        "geckoterminal",
        "tron_rpc",
        "bitcoin_rpc",
    }
    medium_quality = {
        "cryptopanic",
        "reddit",
        "coincap",
        "coinmarketcap",
        "moralis",
        "birdeye",
        "helius",
        "solscan",
        "rmi_intel",
        "scam_detector",
        "rag_similarity",
        "pumpfun",
    }

    high_count = sum(1 for s in sources if s.lower() in high_quality)
    medium_count = sum(1 for s in sources if s.lower() in medium_quality)

    # Score computation
    if source_count >= 4 and high_count >= 2:
        level = "high"
        score = min(100, 70 + source_count * 5)
    elif source_count >= 2 and high_count >= 1:
        level = "medium"
        score = 40 + source_count * 8
    elif source_count >= 1:
        level = "low"
        score = 20 + source_count * 10
    else:
        level = "unverified"
        score = 5

    # Flag freshness
    age_flags = []
    source_lower = [s.lower() for s in sources]
    if "dexscreener" in source_lower:
        age_flags.append("market_data_live")
    if "wallet_labels" in source_lower:
        age_flags.append("labels_cached")

    return {
        "score": min(100, score),
        "level": level,
        "sources_total": source_count,
        "sources_high_quality": high_count,
        "sources_medium_quality": medium_count,
        "flags": age_flags,
        "interpretation": {
            "high": "Multiple verified sources confirm this data",
            "medium": "Adequate coverage from trusted sources",
            "low": "Limited source coverage - verify independently",
            "unverified": "Insufficient data - treat as directional only",
        }.get(level, ""),
    }


__all__ = ["compute_confidence"]
