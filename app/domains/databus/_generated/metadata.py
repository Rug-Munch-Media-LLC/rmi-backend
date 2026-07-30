"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

Chain metadata and data type registry. Provides a lookup of all registered
data types with descriptions, used for introspection and documentation.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

# Descriptive metadata for every registered data type.
# This dict is populated on import from build_provider_chains() output
# for introspection purposes.
from __future__ import annotations

CHAIN_METADATA: dict[str, dict] = {
    "token_price": {
        "description": "Token price across DEXs and CEXs",
        "category": "pricing",
        "providers": ["local_price", "solana_tracker", "dexscreener", "coingecko", "moralis"],
    },
    "market_overview": {
        "description": "Global market cap, BTC/ETH/SOL prices, fear & greed",
        "category": "market",
        "providers": ["rmi_market_overview", "coingecko_global"],
    },
    "trending": {
        "description": "Trending tokens across chains",
        "category": "market",
        "providers": ["rmi_trending", "solana_tracker_trending", "dexscreener_trending"],
    },
    "token_trades": {
        "description": "Live token trades (buys/sells) from DexScreener",
        "category": "trading",
        "providers": ["dexscreener_trades"],
    },
    "top_traders": {
        "description": "Top profitable traders and win rates for a token",
        "category": "trading",
        "providers": ["dexscreener_top_traders"],
    },
    "dune_early_buyers": {
        "description": "First 5-minute buyers of a token (Dune Analytics)",
        "category": "analytics",
        "providers": ["dune_early_buyers_api"],
    },
    "news": {
        "description": "Real-time crypto news from 30+ sources",
        "category": "news",
        "providers": ["rmi_news", "messari_news", "coindesk_news"],
    },
    "alerts": {
        "description": "Active threat alerts from scanner pipeline",
        "category": "security",
        "providers": ["rmi_alerts"],
    },
    "dev_activity": {
        "description": "GitHub developer activity and social volume (Santiment)",
        "category": "analytics",
        "providers": ["santiment_dev"],
    },
    "url_security_scan": {
        "description": "Phishing and malware scan for token websites (VirusTotal)",
        "category": "security",
        "providers": ["virustotal_scan"],
    },
    "holder_data": {
        "description": "Token holder distribution (DexScreener \u2192 Bitquery)",
        "category": "holders",
        "providers": ["dexscreener_holders", "bitquery_holders"],
    },
    "tx_trace": {
        "description": "Transaction traces (Etherscan \u2192 Bitquery)",
        "category": "analytics",
        "providers": ["etherscan_trace", "bitquery_trace"],
    },
    "cross_chain": {
        "description": "Cross-chain transfer tracking and bridge monitoring",
        "category": "analytics",
        "providers": ["bitquery_cross_chain"],
    },
    "wallet_labels": {
        "description": "Address labels and entity identification",
        "category": "identity",
        "providers": ["wallet_memory_bank", "nansen_labels"],
    },
    "scanner": {
        "description": "Token security scan via SENTINEL",
        "category": "security",
        "providers": ["sentinel_scanner"],
    },
    "spl_token_metadata": {
        "description": "Raw SPL token metadata decoder",
        "category": "solana",
        "providers": ["spl_metadata_decoder"],
    },
    "rag_search": {
        "description": "Semantic search across scam documents and patterns",
        "category": "search",
        "providers": ["local_rag"],
    },
    "defillama_tvl": {
        "description": "Global DeFi TVL and top protocols",
        "category": "defi",
        "providers": ["defillama_tvl_api"],
    },
    "defillama_chains": {
        "description": "TVL breakdown by blockchain",
        "category": "defi",
        "providers": ["defillama_chains_api"],
    },
    "blockchair_address": {
        "description": "Multi-chain address balance and tx count",
        "category": "blockchain",
        "providers": ["blockchair_addr_api"],
    },
    "blockchair_stats": {
        "description": "Multi-chain network statistics and mempool data",
        "category": "blockchain",
        "providers": ["blockchair_stats_api"],
    },
    "birdeye_overview": {
        "description": "Token overview, liquidity, and holder stats",
        "category": "pricing",
        "providers": ["birdeye_overview_api"],
    },
    "birdeye_price": {
        "description": "Real-time token price from Birdeye",
        "category": "pricing",
        "providers": ["birdeye_price_api", "dexscreener_price"],
    },
}

# Category groupings for easy access
CATEGORY_INDEX: dict[str, list[str]] = {}
for _key, _meta in CHAIN_METADATA.items():
    CATEGORY_INDEX.setdefault(_meta["category"], []).append(_key)


def get_chains_by_category() -> dict[str, list[str]]:
    """Return a dict mapping category names to lists of data_type keys."""
    return dict(CATEGORY_INDEX)


__all__ = [
    "CATEGORY_INDEX",
    "CHAIN_METADATA",
    "get_chains_by_category",
]
