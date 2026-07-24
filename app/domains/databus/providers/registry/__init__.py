"""
Provider chain registry — builds all fallback chains for every data type.

Every data source in the system is organized into fallback chains:
  1. LOCAL FIRST  — Wallet Memory Bank, ClickHouse, Redis RAG, labels, scanners
  2. FREE SECOND  — DexScreener, Jupiter, DeFiLlama, PublicNode, Binance
  3. PAID LAST    — Arkham, Moralis, Etherscan, CoinGecko Pro
  4. NEVER WASTE  — Pool rotation, rate limits, monthly quota tracking

Split from registry.py into categories/: market, security, wallet, intel, social, system.
"""

from typing import Any

import httpx

from app.core.logging import get_logger

from ..base import Provider, ProviderChain, ProviderTier
from ..circuit_breaker import _circuit_breakers, _CircuitBreaker, _rate_limiters, _RateLimiter

logger = get_logger("databus.providers.registry")


def build_provider_chains() -> dict[str, ProviderChain]:
    """Build all fallback chains for every data type."""
    chains: dict[str, ProviderChain] = {}

    # ── Import all provider functions ──
    from app.databus.spl_metadata_decoder import _spl_metadata_decoder_provider

    from ..chains.alchemy import _alchemy_token_balances, _alchemy_token_metadata
    from ..chains.arkham import (
        _arkham_counterparties,
        _arkham_entity,
        _arkham_intel_search,
        _arkham_labels,
        _arkham_portfolio,
        _arkham_transfers,
    )
    from ..chains.dexscreener import (
        _dexscreener_holders,
        _dexscreener_price,
        _dexscreener_token_metadata,
        _dexscreener_top_traders,
        _dexscreener_trades,
    )
    from ..chains.free_apis import (
        _blockchair_address,
        _blockchair_stats,
        _coingecko_price,
        _defillama_chains,
        _defillama_tvl,
        _etherscan_tx_trace,
    )
    from ..chains.freemium_apis import (
        _birdeye_overview,
        _birdeye_price,
        _coindesk_news,
        _dune_early_buyers,
        _messari_news,
        _santiment_dev_activity,
        _solana_tracker_price,
        _solana_tracker_token,
        _solana_tracker_trending,
        _virustotal_url_scan,
    )
    from ..chains.local import (
        _local_token_price,
        _local_wallet_labels,
        _passthrough_alerts,
        _passthrough_hyperliquid,
        _passthrough_hyperliquid_action,
        _passthrough_insider_wallets,
        _passthrough_market_overview,
        _passthrough_news,
        _passthrough_prediction_signals,
        _passthrough_rag,
        _passthrough_scanner,
        _passthrough_trending,
    )
    from ..chains.mcp_bridge import _mcp_bridge
    from ..chains.moralis import (
        _moralis_price,
        _moralis_search_tokens,
        _moralis_token_metadata,
        _moralis_token_price,
        _moralis_wallet_net_worth,
        _moralis_wallet_nfts,
        _moralis_wallet_tokens,
        _moralis_wallet_transactions,
    )
    from .categories.intel import build_intel_chains

    # ── Build chains by category ──
    from .categories.market import build_market_chains
    from .categories.security import build_security_chains
    from .categories.social import build_social_chains
    from .categories.system import build_system_chains
    from .categories.wallet import build_wallet_chains

    chains.update(build_market_chains(locals()))
    chains.update(build_security_chains(locals()))
    chains.update(build_wallet_chains(locals()))
    chains.update(build_intel_chains(locals()))
    chains.update(build_social_chains(locals()))
    chains.update(build_system_chains(locals()))

    # ── Initialize circuit breakers and rate limiters ──
    for chain in chains.values():
        for provider in chain.providers:
            if provider.name not in _circuit_breakers:
                _circuit_breakers[provider.name] = _CircuitBreaker(
                    threshold=provider.failure_threshold, timeout=provider.recovery_timeout
                )
            if provider.name not in _rate_limiters:
                _rate_limiters[provider.name] = _RateLimiter(rps=provider.rate_limit_rps)

    logger.info(
        f"Built {len(chains)} provider chains with "
        f"{sum(len(c.providers) for c in chains.values())} total providers"
    )
    return chains
