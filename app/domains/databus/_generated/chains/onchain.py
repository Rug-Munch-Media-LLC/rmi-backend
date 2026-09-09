"""
AUTO-GENERATED (split from provider_chains.py) - DO NOT EDIT.

On-chain data provider chains: wallet labels, token scanner, SPL metadata,
RAG search, DeFiLlama TVL, Blockchair stats, and Birdeye overview/price.

Source: app/domains/databus/_generated/provider_chains.py (original god file).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.databus.provider_core import Provider, ProviderChain, ProviderTier
from app.databus.provider_implementations import (
    _birdeye_overview,
    _birdeye_price,
    _blockchair_address,
    _blockchair_stats,
    _defillama_chains,
    _defillama_tvl,
    _dexscreener_price,
    _local_wallet_labels,
    _moralis_price,
    _passthrough_rag,
    _passthrough_scanner,
)
from app.databus.spl_metadata_decoder import _spl_metadata_decoder_provider

# Chain-indexer pattern detection (ClickHouse-powered on-chain behavioral analysis)
from app.domains.databus.providers.chains.lite_chain_indexer import (
    _lite_chain_indexer_pattern_search,
)

logger = get_logger("databus.providers.chains.onchain")


def build_onchain_chains(chains: dict[str, ProviderChain]) -> None:
    """Build wallet, scanner, SPL, RAG, DeFiLlama, Blockchair, and Birdeye chains."""
    # Wallet Labels - OUR data first
    chains["wallet_labels"] = ProviderChain(
        data_type="wallet_labels",
        description="Address labels and entity identification (~140k curated local + external)",
        providers=[
            Provider("wallet_memory_bank", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "nansen_labels",
                ProviderTier.PAID,
                _moralis_price,
                weight=5.0,
                requires_key=True,
                key_env="NANSEN_API_KEY",
            ),
        ],
    )

    # Scanner / Security
    chains["scanner"] = ProviderChain(
        data_type="scanner",
        description="Token security scan via SENTINEL (rug pull, honeypot, risk score)",
        providers=[
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _passthrough_scanner,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── SPL Token Metadata Decoder (FREE, bypasses unreliable 3rd-party APIs) ──
    chains["spl_token_metadata"] = ProviderChain(
        data_type="spl_token_metadata",
        description="Raw SPL token metadata decoder: mint authority, freeze authority, decimals, supply, and Token-2022 extensions",
        providers=[
            Provider(
                "spl_metadata_decoder",
                ProviderTier.FREE_API,
                _spl_metadata_decoder_provider,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    # RAG Search
    chains["rag_search"] = ProviderChain(
        data_type="rag_search",
        description="Semantic search across 17K+ scam documents and patterns",
        providers=[
            Provider("local_rag", ProviderTier.LOCAL, _passthrough_rag, weight=10.0, rate_limit_rps=5.0),
        ],
    )

    # ── DeFiLlama (100% FREE, NO API KEY) ──
    chains["defillama_tvl"] = ProviderChain(
        data_type="defillama_tvl",
        description="Global DeFi TVL and top protocols (completely free)",
        providers=[
            Provider(
                "defillama_tvl_api",
                ProviderTier.FREE_API,
                _defillama_tvl,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )
    chains["defillama_chains"] = ProviderChain(
        data_type="defillama_chains",
        description="TVL breakdown by blockchain (completely free)",
        providers=[
            Provider(
                "defillama_chains_api",
                ProviderTier.FREE_API,
                _defillama_chains,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Blockchair (FREE TIER, NO API KEY FOR BASIC) ──
    chains["blockchair_address"] = ProviderChain(
        data_type="blockchair_address",
        description="Multi-chain address balance and tx count (BTC, ETH, SOL, etc.)",
        providers=[
            Provider(
                "blockchair_addr_api",
                ProviderTier.FREE_API,
                _blockchair_address,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )
    chains["blockchair_stats"] = ProviderChain(
        data_type="blockchair_stats",
        description="Multi-chain network statistics and mempool data",
        providers=[
            Provider(
                "blockchair_stats_api",
                ProviderTier.FREE_API,
                _blockchair_stats,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    # ── Birdeye (FREEMIUM, FREE TIER AVAILABLE) ──
    chains["birdeye_overview"] = ProviderChain(
        data_type="birdeye_overview",
        description="Token overview, liquidity, and holder stats (Solana/EVM)",
        providers=[
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _birdeye_overview,
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
        ],
    )
    chains["birdeye_price"] = ProviderChain(
        data_type="birdeye_price",
        description="Real-time token price from Birdeye",
        providers=[
            Provider(
                "birdeye_price_api",
                ProviderTier.FREEMIUM,
                _birdeye_price,
                weight=5.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
            Provider(
                "dexscreener_price",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=10.0,
                rate_limit_rps=2.0,
            ),  # Fallback to free
        ],
    )

    # ── lite_chain_indexer_patterns — on-chain behavioral scam pattern detection ──
    chains["lite_chain_indexer_patterns"] = ProviderChain(
        "lite_chain_indexer_patterns",
        description="On-chain behavioral scam patterns via chain-indexer ClickHouse (holder concentration, pump-and-dump, deployer clusters, mixer interactions)",
        providers=[
            Provider(
                "lite_chain_indexer_pattern_search",
                ProviderTier.LOCAL,
                _lite_chain_indexer_pattern_search,
                weight=15.0,
                is_local=True,
                description="ClickHouse-powered pattern detection for deployer clusters, holder concentration, pump-and-dump, and mixer interactions",
            ),
        ],
    )

    logger.info(
        "On-chain chains built: wallet_labels, scanner, spl_token_metadata, rag_search, "
        "defillama_tvl, defillama_chains, blockchair_address, blockchair_stats, "
        "birdeye_overview, birdeye_price, lite_chain_indexer_patterns"
    )
