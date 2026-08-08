"""
from typing import Any
x402 Enrichment — Data Models and Constants
"""


from dataclasses import dataclass, field

# ── Which tool categories get enrichment ────────────────────────
# Wallet-centric tools: enrich with labels, scam checking
# Token-centric tools: enrich with holder label analysis
# Market tools: skip (low overlap, not worth the lookup)
ENRICHABLE_CATEGORIES = {
    "wallet": [
        "whale",
        "smartmoney",
        "cluster",
        "insider",
        "copy_trade_finder",
        "wallet",
        "portfolio_tracker",
        "portfolio_aggregate",
        "wallet_pnl",
        "smart_money_alpha",
        "cross_chain_whale",
        "dormant_whale_alert",
        "full_wallet_dossier",
        "wallet_cluster_score",
        "wallet_drain_scanner",
        "wallet_label_registry",
        "whale_network_map",
        "whale_profile",
        "whale_scan",
        "whale_accumulation",
        "sniper_detect",
        "syndicate_scan",
        "syndicate_track",
        "wallet_graph",
        "deployer_history",
        "kol_performance",
        "insider_network",
        "airdrop_finder",
    ],
    "token": [
        "audit",
        "rugshield",
        "forensics",
        "comprehensive_audit",
        "honeypot_check",
        "rug_pull_predictor",
        "token_deep_dive",
        "token_comparison",
        "fresh_pair",
        "clone_detect",
        "launch",
        "launch_intel",
        "sniper_alert",
        "token_age",
        "token_distribution_health",
        "token_velocity",
        "token_watch_create",
        "token_watch_check",
        "token_watch_alerts",
        "token_watch_list",
        "presale_scanner",
        "fair_launch_detect",
        "listing_predictor",
        "arbitrage_scan",
        "scam_database",
        "protocol_risk",
        "liquidity_depth",
        "liquidity_migration",
        "wash_trading",
        "bundler_detect",
        "mev_alert",
        "mev_protection",
        "liquidity_flow",
    ],
    "security": [
        "urlcheck",
        "sentiment",
        "social_signal",
        "anomaly",
        "profile_flip",
        "contract_info",
        "contract_clone_check",
        "slither_audit",
        "storage_reader",
        "tx_decoder",
        "reentrancy_scanner",
        "phantom_mint_detect",
        "tw_profile",
        "tw_timeline",
        "tw_search",
        "bridge_security",
        "nft_wash_detector",
        "risk_monitor",
        "pulse",
        "fresh_pair",
        "sniper_alert",
    ],
    "market": [
        "market_overview",
        "chain_health",
        "defi_yield_scanner",
        "gas_forecast",
        "anomaly_detector",
        "launch_intel",
    ],
}

# Per-chain variants — strip _chain suffix and check base tool name
CHAIN_SUFFIXES = {
    "solana",
    "base",
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "fantom",
    "gnosis",
    "tron",
    "bitcoin",
}

MAX_BATCH_SIZE = 200  # Max addresses per ClickHouse batch query
ENRICH_CACHE_TTL = 3600  # 1 hour
SIMILARITY_THRESHOLD = 0.6  # Cosine similarity floor for RAG matches


@dataclass
class WalletLabel:
    """Lightweight label from ClickHouse lookup."""

    address: str
    label_name: str = ""
    label_category: str = ""
    label_subtype: str = ""
    source: str = ""
    is_sanctioned: bool = False


@dataclass
class EnrichmentResult:
    """Merged enrichment for all addresses in a tool response."""

    labels: dict[str, list[WalletLabel]] = field(default_factory=dict)
    sanctioned: list[str] = field(default_factory=list)
    risk_summary: str = ""
    similar_patterns: list[dict[str, Any]] = field(default_factory=list)
    lookup_ms: float = 0
    rag_ms: float = 0
