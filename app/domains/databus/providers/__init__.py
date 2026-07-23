"""
DataBus Providers v2 - The Complete Data Source Registry

Backward-compat re-export layer. All implementations are in:
  - registry.py — build_provider_chains() with all chain definitions
  - chains/     — individual provider implementations
  - base.py     — Provider, ProviderChain, ProviderTier classes
  - circuit_breaker.py — rate limiting and circuit breaking
"""

from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.providers")

# Re-export from registry
from app.domains.databus.providers.registry import build_provider_chains

# Re-export from base
from app.domains.databus.providers.base import Provider, ProviderChain, ProviderTier

# Re-export from circuit_breaker
from app.domains.databus.providers.circuit_breaker import (
    _CircuitBreaker,
    _RateLimiter,
    _circuit_breakers,
    _rate_limiters,
    _quota_usage,
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
        "_messari_news": "chains.freemium_apis",
        "_coindesk_news": "chains.freemium_apis",
        "_santiment_dev_activity": "chains.freemium_apis",
        "_virustotal_url_scan": "chains.freemium_apis",
        "_dune_early_buyers": "chains.freemium_apis",
        # MCP Bridge
        "_mcp_bridge": "chains.mcp_bridge",
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
    # Circuit breaker
    "_CircuitBreaker",
    "_circuit_breakers",
    "_RateLimiter",
    "_rate_limiters",
    "_quota_usage",
    # Main builder
    "build_provider_chains",
    # Provider functions (lazy-loaded)
    "_local_token_price",
    "_local_wallet_labels",
    "_passthrough_market_overview",
    "_passthrough_trending",
    "_passthrough_news",
    "_passthrough_alerts",
    "_passthrough_scanner",
    "_passthrough_rag",
    "_dexscreener_price",
    "_dexscreener_token_metadata",
    "_dexscreener_holders",
    "_dexscreener_trades",
    "_dexscreener_top_traders",
    "_coingecko_price",
    "_defillama_tvl",
    "_defillama_chains",
    "_blockchair_address",
    "_blockchair_stats",
    "_etherscan_tx_trace",
    "_moralis_price",
    "_moralis_wallet_tokens",
    "_moralis_wallet_nfts",
    "_moralis_wallet_transactions",
    "_moralis_token_price",
    "_moralis_token_metadata",
    "_moralis_wallet_net_worth",
    "_moralis_search_tokens",
    "_alchemy_token_balances",
    "_alchemy_token_metadata",
    "_arkham_entity",
    "_arkham_counterparties",
    "_arkham_intel_search",
    "_arkham_portfolio",
    "_arkham_transfers",
    "_arkham_labels",
    "_birdeye_overview",
    "_birdeye_price",
    "_solana_tracker_price",
    "_solana_tracker_token",
    "_solana_tracker_trending",
    "_messari_news",
    "_coindesk_news",
    "_santiment_dev_activity",
    "_virustotal_url_scan",
    "_dune_early_buyers",
    "_mcp_bridge",
    "_spl_metadata_decoder_provider",
]
