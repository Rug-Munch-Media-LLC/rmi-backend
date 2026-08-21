"""
DataBus Providers v2 - The Complete Data Source Registry

Backward-compat re-export layer. All implementations are in:
  - registry.py — build_provider_chains() with all chain definitions
  - chains/     — individual provider implementations
  - base.py     — Provider, ProviderChain, ProviderTier classes
  - circuit_breaker.py — rate limiting and circuit breaking
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.providers")

# Re-export from registry
# Re-export from base
from app.domains.databus.providers.base import (  # noqa: E402 — imported after logger init; kept below module header for readability
    Provider,
    ProviderChain,
    ProviderTier,
)

# Re-export from circuit_breaker
from app.domains.databus.providers.circuit_breaker import (  # noqa: E402 — module docstring precedes import block
    _circuit_breakers,
    _quota_usage,
    _rate_limiters,
    _RateLimiter,
)
from app.domains.databus.providers.registry import (  # noqa: E402 — lazy import block kept below module init
    build_provider_chains,
)

# Lazy-import provider functions on demand
_imported_names = {}


def __getattr__(name: str) -> Any:
    """Lazy-import provider functions from chains/ modules."""
    if name in _imported_names:
        return _imported_names[name]

    # Map function names to their chain modules
    _chain_map = {
        # Local
        "_local_token_price": "chains.local",
        "_local_wallet_labels": "chains.local",
        "_clickhouse_wallet_labels": "chains.local",
        "_passthrough_market_overview": "chains.local",
        "_passthrough_trending": "chains.local",
        "_passthrough_news": "chains.local",
        "_passthrough_alerts": "chains.local",
        "_passthrough_scanner": "chains.local",
        "_passthrough_rag": "chains.local",
        "_passthrough_hyperliquid": "chains.local",
        "_passthrough_insider_wallets": "chains.local",
        "_passthrough_prediction_signals": "chains.local",
        # DexScreener
        "_dexscreener_price": "chains.dexscreener",
        "_dexscreener_token_metadata": "chains.dexscreener",
        "_dexscreener_holders": "chains.dexscreener",
        "_dexscreener_trades": "chains.dexscreener",
        "_dexscreener_top_traders": "chains.dexscreener",
        # Free APIs
        "_coingecko_price": "chains.free_apis",
        "_defillama_tvl": "chains.free_apis",
        "_defillama_chains": "chains.free_apis",
        "_blockchair_address": "chains.free_apis",
        "_blockchair_stats": "chains.free_apis",
        "_etherscan_tx_trace": "chains.free_apis",
        # Moralis
        "_moralis_price": "chains.moralis",
        "_moralis_wallet_tokens": "chains.moralis",
        "_moralis_wallet_nfts": "chains.moralis",
        "_moralis_wallet_transactions": "chains.moralis",
        "_moralis_token_price": "chains.moralis",
        "_moralis_token_metadata": "chains.moralis",
        "_moralis_wallet_net_worth": "chains.moralis",
        "_moralis_search_tokens": "chains.moralis",
        # Alchemy
        "_alchemy_token_balances": "chains.alchemy",
        "_alchemy_token_metadata": "chains.alchemy",
        # Arkham
        "_arkham_entity": "chains.arkham",
        "_arkham_counterparties": "chains.arkham",
        "_arkham_intel_search": "chains.arkham",
        "_arkham_portfolio": "chains.arkham",
        "_arkham_transfers": "chains.arkham",
        "_arkham_labels": "chains.arkham",
        # Freemium APIs
        "_birdeye_overview": "chains.freemium_apis",
        "_birdeye_price": "chains.freemium_apis",
        "_solana_tracker_price": "chains.freemium_apis",
        "_solana_tracker_token": "chains.freemium_apis",
        "_solana_tracker_trending": "chains.freemium_apis",
        "_gmgn_trending": "chains.freemium_apis",
        "_messari_news": "chains.freemium_apis",
        "_coindesk_news": "chains.freemium_apis",
        "_santiment_dev_activity": "chains.freemium_apis",
        "_virustotal_url_scan": "chains.freemium_apis",
        "_dune_early_buyers": "chains.freemium_apis",
        # MCP Bridge
        "_mcp_bridge": "chains.mcp_bridge",
        # Intel providers
        "_scraped_intel_provider": "chains.intel_provider",
        # Chain Indexer ClickHouse providers
        "_lite_chain_indexer_token_transfers": "chains.lite_chain_indexer",
        "_lite_chain_indexer_dex_events": "chains.lite_chain_indexer",
        "_lite_chain_indexer_deployer_history": "chains.lite_chain_indexer",
        "_lite_chain_indexer_solana_balance_changes": "chains.lite_chain_indexer",
        "_lite_chain_indexer_holders": "chains.lite_chain_indexer",
        "_lite_chain_indexer_pattern_search": "chains.lite_chain_indexer",
        # Free security / wallet / market fallback providers
        # (degodding phase 3: split from chains.free_security into focused
        # modules; chains.free_security remains a re-export hub)
        "_free_security_clickhouse": "chains.free_security_tokens",
        "_free_security_goplus": "chains.free_security_tokens",
        "_free_security_honeypot_is": "chains.free_security_tokens",
        "_free_security_rugcheck": "chains.free_security_tokens",
        "_free_security_dexscreener": "chains.free_security_tokens",
        "_free_blockscout_labels": "chains.free_wallet_labels",
        "_free_etherscan_labels": "chains.free_wallet_labels",
        "_free_wallet_pattern_heuristics": "chains.free_wallet_labels",
        "_free_market_clickhouse_twap": "chains.free_market_price",
        "_free_market_coingecko": "chains.free_market_price",
        "_free_market_geckoterminal": "chains.free_market_price",
        "_free_market_paraswap_quote": "chains.free_market_price",
        "_free_market_oneinch_quote": "chains.free_market_price",
        # GMGN public API (Tier 3 fallback — free, no key)
        "_gmgn_token_info": "chains.gmgn",
        "_gmgn_wallet_tx_history": "chains.gmgn",
        "_gmgn_token_holders": "chains.gmgn",
        "_gmgn_new_pairs": "chains.gmgn",
        "_gmgn_bonded_pairs": "chains.gmgn",
        "_gmgn_token_trades": "chains.gmgn",
        # Chain Indexer own-data providers (Phase 4 — LOCAL tier)
        "_lite_chain_indexer_deployer_reputation": "chains.lite_chain_indexer_own",
        "_lite_chain_indexer_whale_detect": "chains.lite_chain_indexer_own",
        "_lite_chain_indexer_volume_authenticity": "chains.lite_chain_indexer_own",
        "_lite_chain_indexer_holder_age": "chains.lite_chain_indexer_own",
        # C6 — time-based attack detection (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_attack_detection": "chains.lite_chain_indexer_own",
        # C7 — deployer behavioral fingerprint (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_deployer_fingerprint": "chains.lite_chain_indexer_own",
        # C1 — wallet-to-wallet flow visualization (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_wallet_flow": "chains.lite_chain_indexer_own",
        # C3 — new token radar / early warning (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_new_token_radar": "chains.lite_chain_indexer_own",
        # C5 — hype score (LOCAL tier, ClickHouse + free trending)
        "_lite_chain_indexer_hype_score": "chains.lite_chain_indexer_own",
        # C2 — BubbleMaps-style holder clustering (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_holder_clusters": "chains.lite_chain_indexer_own",
        # C4 — cross-chain entity resolution v1 (LOCAL tier, ClickHouse-only)
        "_lite_chain_indexer_cross_chain_entity": "chains.lite_chain_indexer_own",
        # F2 price/decimals oracle providers (LOCAL tier)
        "token_price_local": "chains.price_oracle_provider",
        "token_meta_local": "chains.price_oracle_provider",
    }

    module_path = _chain_map.get(name)
    if module_path:
        import importlib

        mod = importlib.import_module(f"app.domains.databus.providers.{module_path}")
        val = getattr(mod, name)
        _imported_names[name] = val
        return val

    # Special case: SPL metadata decoder
    if name == "_spl_metadata_decoder_provider":
        import importlib

        mod = importlib.import_module("app.databus.spl_metadata_decoder")
        val = getattr(mod, name)
        _imported_names[name] = val
        return val

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core classes
    "Provider",
    "ProviderChain",
    "ProviderTier",
    # Rate limiter
    "_RateLimiter",
    "_alchemy_token_balances",
    "_alchemy_token_metadata",
    "_arkham_counterparties",
    "_arkham_entity",
    "_arkham_intel_search",
    "_arkham_labels",
    "_arkham_portfolio",
    "_arkham_transfers",
    "_birdeye_overview",
    "_birdeye_price",
    "_blockchair_address",
    "_blockchair_stats",
    "_circuit_breakers",
    "_clickhouse_wallet_labels",
    "_coindesk_news",
    "_coingecko_price",
    "_defillama_chains",
    "_defillama_tvl",
    "_dexscreener_holders",
    "_dexscreener_price",
    "_dexscreener_token_metadata",
    "_dexscreener_top_traders",
    "_dexscreener_trades",
    "_dune_early_buyers",
    "_etherscan_tx_trace",
    "_free_blockscout_labels",
    "_free_etherscan_labels",
    "_free_market_clickhouse_twap",
    "_free_market_coingecko",
    "_free_market_geckoterminal",
    "_free_market_oneinch_quote",
    "_free_market_paraswap_quote",
    "_free_security_clickhouse",
    "_free_security_dexscreener",
    "_free_security_goplus",
    "_free_security_honeypot_is",
    "_free_security_rugcheck",
    "_free_wallet_pattern_heuristics",
    # GMGN public API providers (Tier 3 fallback)
    "_gmgn_bonded_pairs",
    "_gmgn_new_pairs",
    "_gmgn_token_holders",
    "_gmgn_token_info",
    "_gmgn_token_trades",
    "_gmgn_trending",
    "_gmgn_wallet_tx_history",
    "_lite_chain_indexer_attack_detection",
    "_lite_chain_indexer_cross_chain_entity",
    "_lite_chain_indexer_deployer_fingerprint",
    "_lite_chain_indexer_deployer_history",
    "_lite_chain_indexer_deployer_reputation",
    "_lite_chain_indexer_dex_events",
    "_lite_chain_indexer_holder_age",
    "_lite_chain_indexer_holder_clusters",
    "_lite_chain_indexer_holders",
    "_lite_chain_indexer_hype_score",
    "_lite_chain_indexer_new_token_radar",
    "_lite_chain_indexer_pattern_search",
    "_lite_chain_indexer_solana_balance_changes",
    "_lite_chain_indexer_token_transfers",
    "_lite_chain_indexer_volume_authenticity",
    "_lite_chain_indexer_wallet_flow",
    "_lite_chain_indexer_whale_detect",
    # Provider functions (lazy-loaded)
    "_local_token_price",
    "_local_wallet_labels",
    "_mcp_bridge",
    "_messari_news",
    "_moralis_price",
    "_moralis_search_tokens",
    "_moralis_token_metadata",
    "_moralis_token_price",
    "_moralis_wallet_net_worth",
    "_moralis_wallet_nfts",
    "_moralis_wallet_tokens",
    "_moralis_wallet_transactions",
    "_passthrough_alerts",
    "_passthrough_market_overview",
    "_passthrough_news",
    "_passthrough_rag",
    "_passthrough_scanner",
    "_passthrough_trending",
    "_quota_usage",
    "_rate_limiters",
    "_santiment_dev_activity",
    "_scraped_intel_provider",
    "_solana_tracker_price",
    "_solana_tracker_token",
    "_solana_tracker_trending",
    "_spl_metadata_decoder_provider",
    "_virustotal_url_scan",
    # Main builder
    "build_provider_chains",
    "token_meta_local",
    "token_price_local",
]
