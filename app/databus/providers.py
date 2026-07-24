"""Backward-compat shim — moved to app.domains.databus.providers in P4.

Phase 4 of AUDIT-2026-Q3.md moved app/databus/providers/ to
app/domains/databus/providers/. This shim uses lazy imports to
avoid circular import with app.databus.core.
"""

from __future__ import annotations
import importlib

# All names that come from app.databus.provider_core
_CORE_NAMES = frozenset({
    "Provider", "ProviderChain", "ProviderTier",
    "_CircuitBreaker", "_circuit_breakers",
    "_RateLimiter", "_rate_limiters", "_quota_usage",
})

# All names that come from app.databus.provider_implementations
_IMPL_NAMES = frozenset({
    "_local_token_price", "_dexscreener_price", "_coingecko_price",
    "_moralis_price",
    "_alchemy_token_balances", "_alchemy_token_metadata",
    "_passthrough_market_overview", "_passthrough_trending",
    "_passthrough_news", "_passthrough_alerts",
    "_passthrough_scanner", "_passthrough_rag",
    "_local_wallet_labels",
    "_moralis_wallet_tokens", "_moralis_wallet_nfts",
    "_moralis_wallet_transactions",
    "_moralis_token_price", "_moralis_token_metadata",
    "_moralis_wallet_net_worth", "_moralis_search_tokens",
    "_mcp_bridge",
    "_arkham_entity", "_arkham_counterparties",
    "_arkham_intel_search", "_arkham_portfolio",
    "_arkham_transfers", "_arkham_labels",
    "_dexscreener_token_metadata", "_dexscreener_holders",
    "_dexscreener_trades", "_dexscreener_top_traders",
    "_etherscan_tx_trace",
    "_defillama_tvl", "_defillama_chains",
    "_blockchair_address", "_blockchair_stats",
    "_birdeye_overview", "_birdeye_price",
    "_solana_tracker_price", "_solana_tracker_token",
    "_solana_tracker_trending",
    "_messari_news", "_coindesk_news",
    "_santiment_dev_activity", "_virustotal_url_scan",
    "_dune_early_buyers",
})

# All names from app.databus.spl_metadata_decoder
_SPL_NAMES = frozenset({"_spl_metadata_decoder_provider"})

# build_provider_chains comes from app.domains.databus.providers
_DOMAIN_NAMES = frozenset({"build_provider_chains"})

_all_names = frozenset.union(_CORE_NAMES, _IMPL_NAMES, _SPL_NAMES, _DOMAIN_NAMES)

def __getattr__(name):
    if name in _CORE_NAMES:
        mod = importlib.import_module("app.databus.provider_core")
        return getattr(mod, name)
    if name in _IMPL_NAMES:
        mod = importlib.import_module("app.databus.provider_implementations")
        return getattr(mod, name)
    if name in _SPL_NAMES:
        mod = importlib.import_module("app.databus.spl_metadata_decoder")
        return getattr(mod, name)
    if name in _DOMAIN_NAMES:
        mod = importlib.import_module("app.domains.databus.providers")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = sorted(_all_names)
