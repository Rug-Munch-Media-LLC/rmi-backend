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
from typing import TYPE_CHECKING

from app.core.circuit_breaker import SimpleCircuitBreaker
from app.core.logging import get_logger

from ..circuit_breaker import _circuit_breakers, _rate_limiters, _RateLimiter

if TYPE_CHECKING:
    from ..base import ProviderChain

logger = get_logger("databus.providers.registry")


def build_provider_chains() -> dict[str, ProviderChain]:
    """Build all fallback chains for every data type."""
    chains: dict[str, ProviderChain] = {}

    # ── Import all provider functions ──
    from app.databus.spl_metadata_decoder import (
        _spl_metadata_decoder_provider,  # noqa: F401 — consumed by category builders via locals()
    )

    from ..chains.alchemy import (  # noqa: F401 — consumed by category builders via locals()
        _alchemy_token_balances,
        _alchemy_token_metadata,
    )
    from ..chains.arkham import (
        _arkham_counterparties,  # noqa: F401 — consumed by category builders via locals()
        _arkham_entity,  # noqa: F401 — consumed by category builders via locals()
        _arkham_intel_search,  # noqa: F401 — consumed by category builders via locals()
        _arkham_labels,  # noqa: F401 — consumed by category builders via locals()
        _arkham_portfolio,  # noqa: F401 — consumed by category builders via locals()
        _arkham_transfers,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.chain_indexer import (
        _chain_indexer_deployer_history,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_dex_events,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_holders,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_pattern_search,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_solana_balance_changes,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_token_transfers,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.chain_indexer_own import (
        _chain_indexer_attack_detection,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_cross_chain_entity,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_deployer_fingerprint,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_deployer_reputation,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_holder_age,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_holder_clusters,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_hype_score,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_new_token_radar,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_volume_authenticity,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_wallet_flow,  # noqa: F401 — consumed by category builders via locals()
        _chain_indexer_whale_detect,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.dexscreener import (
        _dexscreener_holders,  # noqa: F401 — consumed by category builders via locals()
        _dexscreener_price,  # noqa: F401 — consumed by category builders via locals()
        _dexscreener_token_metadata,  # noqa: F401 — consumed by category builders via locals()
        _dexscreener_top_traders,  # noqa: F401 — consumed by category builders via locals()
        _dexscreener_trades,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.free_apis import (
        _blockchair_address,  # noqa: F401 — consumed by category builders via locals()
        _blockchair_stats,  # noqa: F401 — consumed by category builders via locals()
        _coingecko_price,  # noqa: F401 — consumed by category builders via locals()
        _defillama_chains,  # noqa: F401 — consumed by category builders via locals()
        _defillama_tvl,  # noqa: F401 — consumed by category builders via locals()
        _etherscan_tx_trace,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.free_security import (
        _free_blockscout_labels,  # noqa: F401 — consumed by category builders via locals()
        _free_etherscan_labels,  # noqa: F401 — consumed by category builders via locals()
        _free_market_clickhouse_twap,  # noqa: F401 — consumed by category builders via locals()
        _free_market_coingecko,  # noqa: F401 — consumed by category builders via locals()
        _free_market_geckoterminal,  # noqa: F401 — consumed by category builders via locals()
        _free_market_oneinch_quote,  # noqa: F401 — consumed by category builders via locals()
        _free_market_paraswap_quote,  # noqa: F401 — consumed by category builders via locals()
        _free_security_clickhouse,  # noqa: F401 — consumed by category builders via locals()
        _free_security_dexscreener,  # noqa: F401 — consumed by category builders via locals()
        _free_security_goplus,  # noqa: F401 — consumed by category builders via locals()
        _free_security_honeypot_is,  # noqa: F401 — consumed by category builders via locals()
        _free_security_rugcheck,  # noqa: F401 — consumed by category builders via locals()
        _free_wallet_pattern_heuristics,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.freemium_apis import (
        _birdeye_overview,  # noqa: F401 — consumed by category builders via locals()
        _birdeye_price,  # noqa: F401 — consumed by category builders via locals()
        _coindesk_news,  # noqa: F401 — consumed by category builders via locals()
        _dune_early_buyers,  # noqa: F401 — consumed by category builders via locals()
        _messari_news,  # noqa: F401 — consumed by category builders via locals()
        _santiment_dev_activity,  # noqa: F401 — consumed by category builders via locals()
        _solana_tracker_price,  # noqa: F401 — consumed by category builders via locals()
        _solana_tracker_token,  # noqa: F401 — consumed by category builders via locals()
        _solana_tracker_trending,  # noqa: F401 — consumed by category builders via locals()
        _virustotal_url_scan,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.intel_provider import (
        _scraped_intel_provider,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.local import (
        _clickhouse_wallet_labels,  # noqa: F401 — consumed by category builders via locals()
        _local_token_price,  # noqa: F401 — consumed by category builders via locals()
        _local_wallet_labels,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_alerts,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_hyperliquid,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_hyperliquid_action,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_insider_wallets,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_market_overview,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_news,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_prediction_signals,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_rag,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_scanner,  # noqa: F401 — consumed by category builders via locals()
        _passthrough_trending,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.mcp_bridge import _mcp_bridge  # noqa: F401 — consumed by category builders via locals()
    from ..chains.moralis import (
        _moralis_price,  # noqa: F401 — consumed by category builders via locals()
        _moralis_search_tokens,  # noqa: F401 — consumed by category builders via locals()
        _moralis_token_metadata,  # noqa: F401 — consumed by category builders via locals()
        _moralis_token_price,  # noqa: F401 — consumed by category builders via locals()
        _moralis_wallet_net_worth,  # noqa: F401 — consumed by category builders via locals()
        _moralis_wallet_nfts,  # noqa: F401 — consumed by category builders via locals()
        _moralis_wallet_tokens,  # noqa: F401 — consumed by category builders via locals()
        _moralis_wallet_transactions,  # noqa: F401 — consumed by category builders via locals()
    )
    from ..chains.price_oracle_provider import (  # noqa: F401 — consumed by category builders via locals()
        token_meta_local,
        token_price_local,
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
