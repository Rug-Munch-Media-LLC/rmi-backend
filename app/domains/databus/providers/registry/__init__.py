"""
Provider chain registry — builds all fallback chains for every data type.

Every data source in the system is organized into fallback chains:
  1. LOCAL FIRST  — Wallet Memory Bank, ClickHouse, Redis RAG, labels, scanners
  2. FREE SECOND  — DexScreener, Jupiter, DeFiLlama, PublicNode, Binance
  3. PAID LAST    — Arkham, Moralis, Etherscan, CoinGecko Pro
  4. NEVER WASTE  — Pool rotation, rate limits, monthly quota tracking

Split from registry.py into categories/: market, security, wallet, intel, social, system.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

import httpx

from app.core.circuit_breaker import SimpleCircuitBreaker
from app.core.logging import get_logger

from ..base import Provider, ProviderTier
from ..circuit_breaker import _circuit_breakers, _rate_limiters, _RateLimiter

if TYPE_CHECKING:
    from ..base import ProviderChain

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
    from ..chains.intel_provider import _scraped_intel_provider
    from ..chains.local import (
        _clickhouse_wallet_labels,
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

    # ── Build chains by category ──
    _builders: list[tuple[str, str]] = [
        ("market", ".categories.market"),
        ("security", ".categories.security"),
        ("wallet", ".categories.wallet"),
        ("intel", ".categories.intel"),
        ("social", ".categories.social"),
        ("system", ".categories.system"),
    ]
    for name, mod_path in _builders:
        try:
            mod = importlib.import_module(mod_path, __package__)
            builder = getattr(mod, f"build_{name}_chains", None)
            if builder:
                chains.update(builder(locals()))
        except Exception as exc:
            logger.warning(f"Chain builder '{name}' failed: {exc}")

    # ── Generated / optional chains ──
    for _builder_module in (
        "app.domains.databus._generated.chains.bitquery",
        "app.domains.databus._generated.chains.premium",
    ):
        try:
            mod = importlib.import_module(_builder_module)
            builder_name = f"build_{_builder_module.rsplit('.', 1)[-1]}_chains"
            builder = getattr(mod, builder_name, None)
            if builder:
                builder(chains)
        except Exception:
            logger.debug(f"Optional chain builder {_builder_module} not available")

    # ── Initialize circuit breakers and rate limiters ──
    for chain in chains.values():
        for provider in chain.providers:
            if provider.name not in _circuit_breakers:
                _circuit_breakers[provider.name] = SimpleCircuitBreaker(
                    name=provider.name,
                    failure_threshold=provider.failure_threshold,
                    recovery_timeout=provider.recovery_timeout,
                )
            if provider.name not in _rate_limiters:
                _rate_limiters[provider.name] = _RateLimiter(rps=provider.rate_limit_rps)

    logger.info(
        f"Built {len(chains)} provider chains with {sum(len(c.providers) for c in chains.values())} total providers"
    )
    return chains
