"""
Provider chain registry — builds all fallback chains for every data type.

This module contains build_provider_chains() which composes Provider objects
into ProviderChains with intelligent fallback ordering.

Every data source in the system is organized into fallback chains:
  1. LOCAL FIRST  — Wallet Memory Bank, ClickHouse, Redis RAG, labels, scanners
  2. FREE SECOND  — DexScreener, Jupiter, DeFiLlama, PublicNode, Binance
  3. PAID LAST    — Arkham, Moralis, Etherscan, CoinGecko Pro
  4. NEVER WASTE  — Pool rotation, rate limits, monthly quota tracking
"""

from typing import Any
import httpx

from app.core.logging import get_logger

from .base import Provider, ProviderChain, ProviderTier
from .circuit_breaker import _CircuitBreaker, _RateLimiter, _circuit_breakers, _rate_limiters

logger = get_logger("databus.providers.registry")


def build_provider_chains() -> dict[str, ProviderChain]:
    """Build all fallback chains for every data type."""
    chains: dict[str, ProviderChain] = {}

    # ── Import all provider functions ──
    from .chains.local import (
        _passthrough_hyperliquid,
        _passthrough_hyperliquid_action,
        _passthrough_insider_wallets,
        _passthrough_prediction_signals,
        _local_token_price,
        _local_wallet_labels,
        _passthrough_alerts,
        _passthrough_market_overview,
        _passthrough_news,
        _passthrough_rag,
        _passthrough_scanner,
        _passthrough_trending,
    )
    from .chains.dexscreener import (
        _dexscreener_holders,
        _dexscreener_price,
        _dexscreener_token_metadata,
        _dexscreener_top_traders,
        _dexscreener_trades,
    )
    from .chains.free_apis import (
        _blockchair_address,
        _blockchair_stats,
        _coingecko_price,
        _defillama_chains,
        _defillama_tvl,
        _etherscan_tx_trace,
    )
    from .chains.moralis import (
        _moralis_price,
        _moralis_search_tokens,
        _moralis_token_metadata,
        _moralis_token_price,
        _moralis_wallet_net_worth,
        _moralis_wallet_nfts,
        _moralis_wallet_tokens,
        _moralis_wallet_transactions,
    )
    from .chains.alchemy import _alchemy_token_balances, _alchemy_token_metadata
    from .chains.arkham import (
        _arkham_counterparties,
        _arkham_entity,
        _arkham_intel_search,
        _arkham_labels,
        _arkham_portfolio,
        _arkham_transfers,
    )
    from .chains.freemium_apis import (
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
    from .chains.mcp_bridge import _mcp_bridge

    # Import SPL metadata decoder (external module, not in chains/)
    from app.databus.spl_metadata_decoder import _spl_metadata_decoder_provider  # noqa: E402

    # ── Token Price ──
    chains["token_price"] = ProviderChain(
        data_type="token_price",
        description="Token price across DEXs and CEXs",
        providers=[
            Provider("local_price", ProviderTier.LOCAL, _local_token_price, weight=10.0),
            Provider(
                "solana_tracker",
                ProviderTier.FREEMIUM,
                _solana_tracker_price,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=2.0,
            ),
            Provider("coingecko", ProviderTier.FREE_API, _coingecko_price, weight=5.0),
            Provider(
                "moralis",
                ProviderTier.PAID,
                _moralis_price,
                weight=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
                monthly_quota=5000,
            ),
        ],
    )

    # ── Market Overview ──
    chains["market_overview"] = ProviderChain(
        data_type="market_overview",
        description="Global market cap, BTC/ETH/SOL prices, fear & greed",
        providers=[
            Provider(
                "rmi_market_overview", ProviderTier.LOCAL, _passthrough_market_overview, weight=10.0
            ),
            Provider(
                "coingecko_global",
                ProviderTier.FREEMIUM,
                _coingecko_price,
                weight=5.0,
                requires_key=True,
                key_env="COINGECKO_API_KEY",
            ),
        ],
    )

    # ── Trending ──
    chains["trending"] = ProviderChain(
        data_type="trending",
        description="Trending tokens across chains",
        providers=[
            Provider("rmi_trending", ProviderTier.LOCAL, _passthrough_trending, weight=10.0),
            Provider(
                "solana_tracker_trending",
                ProviderTier.FREEMIUM,
                _solana_tracker_trending,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener_trending",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=1.0,
            ),
        ],
    )

    # ── Token Trades (Live buys/sells) ──
    chains["token_trades"] = ProviderChain(
        data_type="token_trades",
        description="Live token trades (buys/sells) from DexScreener",
        providers=[
            Provider(
                "dexscreener_trades",
                ProviderTier.FREE_API,
                _dexscreener_trades,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Top Traders ──
    chains["top_traders"] = ProviderChain(
        data_type="top_traders",
        description="Top profitable traders and win rates for a token",
        providers=[
            Provider(
                "dexscreener_top_traders",
                ProviderTier.FREE_API,
                _dexscreener_top_traders,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Dune Analytics ──
    chains["dune_early_buyers"] = ProviderChain(
        data_type="dune_early_buyers",
        description="First 5-minute buyers of a token (Dune Analytics, low-CU cached query)",
        providers=[
            Provider(
                "dune_early_buyers_api",
                ProviderTier.FREEMIUM,
                _dune_early_buyers,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="DUNE_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # ── News ──
    chains["news"] = ProviderChain(
        data_type="news",
        description="Real-time crypto news from 30+ sources + Messari + CoinDesk institutional feeds",
        providers=[
            Provider("rmi_news", ProviderTier.LOCAL, _passthrough_news, weight=10.0),
            Provider(
                "messari_news",
                ProviderTier.FREEMIUM,
                _messari_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MESSARI_API_KEY",
                monthly_quota=10000,
            ),
            Provider(
                "coindesk_news",
                ProviderTier.FREEMIUM,
                _coindesk_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="COINDESK_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # ── Alerts / Live Intel ──
    chains["alerts"] = ProviderChain(
        data_type="alerts",
        description="Active threat alerts from scanner pipeline",
        providers=[
            Provider("rmi_alerts", ProviderTier.LOCAL, _passthrough_alerts, weight=10.0),
        ],
    )

    # ── Dev Activity ──
    chains["dev_activity"] = ProviderChain(
        data_type="dev_activity",
        description="GitHub developer activity and social volume (Santiment free tier)",
        providers=[
            Provider(
                "santiment_dev",
                ProviderTier.FREEMIUM,
                _santiment_dev_activity,
                weight=10.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="SANTIMENT_API_KEY",
                monthly_quota=3000,
            ),
        ],
    )

    # ── URL Security Scan ──
    chains["url_security_scan"] = ProviderChain(
        data_type="url_security_scan",
        description="Phishing and malware scan for token websites (VirusTotal free tier)",
        providers=[
            Provider(
                "virustotal_scan",
                ProviderTier.FREEMIUM,
                _virustotal_url_scan,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="VIRUSTOTAL_API_KEY",
                monthly_quota=15000,
            ),
        ],
    )

    # ── Bitquery chains (conditional on availability) ──
    try:
        from app.databus.bitquery_provider import BitqueryProvider

        _bq = BitqueryProvider()

        async def _bq_token_price(**kw: Any) -> Any:
            return await _bq.get_token_price(kw.get("network", "ethereum"), kw.get("token", ""))

        async def _bq_holder_data(**kw: Any) -> Any:
            return await _bq.get_holder_distribution(
                kw.get("network", "ethereum"), kw.get("token", "")
            )

        async def _bq_tx_trace(**kw: Any) -> Any:
            return await _bq.get_transaction_trace(
                kw.get("network", "ethereum"), kw.get("tx_hash", "")
            )

        async def _bq_dex_volume(**kw: Any) -> Any:
            return await _bq.get_dex_volume(kw.get("network", "ethereum"))

        async def _bq_address_balance(**kw: Any) -> Any:
            return await _bq.get_address_balance(
                kw.get("network", "ethereum"), kw.get("address", "")
            )

        async def _bq_cross_chain(**kw: Any) -> Any:
            return await _bq.get_cross_chain_transfers(kw.get("address", ""))

        chains["holder_data"] = ProviderChain(
            "holder_data",
            description="Token holder distribution (DexScreener free → Bitquery freemium)",
            providers=[
                Provider(
                    "dexscreener_holders",
                    ProviderTier.FREE_API,
                    _dexscreener_holders,
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "bitquery_holders",
                    ProviderTier.FREEMIUM,
                    _bq_holder_data,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["tx_trace"] = ProviderChain(
            "tx_trace",
            description="Transaction traces (Etherscan free → Bitquery freemium)",
            providers=[
                Provider(
                    "etherscan_trace",
                    ProviderTier.FREE_API,
                    _etherscan_tx_trace,
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "bitquery_trace",
                    ProviderTier.FREEMIUM,
                    _bq_tx_trace,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["cross_chain"] = ProviderChain(
            "cross_chain",
            description="Cross-chain transfer tracking and bridge monitoring",
            providers=[
                Provider(
                    "bitquery_cross_chain",
                    ProviderTier.FREEMIUM,
                    _bq_cross_chain,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                )
            ],
        )
        logger.info("Bitquery chains registered: holder_data, tx_trace, cross_chain")
    except Exception as e:
        logger.warning(f"Bitquery not available: {e}")

    # ── Wallet Labels ──
    chains["wallet_labels"] = ProviderChain(
        data_type="wallet_labels",
        description="Address labels and entity identification (190K local + external)",
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

    # ── Scanner / Security ──
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

    # ── SPL Token Metadata Decoder ──
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

    # ── RAG Search ──
    chains["rag_search"] = ProviderChain(
        data_type="rag_search",
        description="Semantic search across 17K+ scam documents and patterns",
        providers=[
            Provider(
                "local_rag", ProviderTier.LOCAL, _passthrough_rag, weight=10.0, rate_limit_rps=5.0
            ),
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
            ),
        ],
    )

    # ── Alchemy chains ──
    chains["wallet_tokens"] = ProviderChain(
        data_type="wallet_tokens",
        description="Token balances for any address (Alchemy + Moralis)",
        providers=[
            Provider(
                "alchemy_balances",
                ProviderTier.FREEMIUM,
                _alchemy_token_balances,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
            Provider(
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["token_metadata"] = ProviderChain(
        data_type="token_metadata",
        description="Token metadata lookup (DexScreener free → Alchemy freemium)",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _dexscreener_token_metadata,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "alchemy_metadata",
                ProviderTier.FREEMIUM,
                _alchemy_token_metadata,
                weight=5.0,
                rate_limit_rps=10.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
        ],
    )
    chains["wallet_nfts"] = ProviderChain(
        data_type="wallet_nfts",
        description="NFT holdings for any address (Moralis - free tier available)",
        providers=[
            Provider(
                "moralis_nfts",
                ProviderTier.FREEMIUM,
                _moralis_wallet_nfts,
                weight=5.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Moralis expanded chains ──
    chains["wallet_transactions"] = ProviderChain(
        data_type="wallet_transactions",
        description="Wallet transaction history (Etherscan free → Moralis freemium)",
        providers=[
            Provider(
                "etherscan_wallet",
                ProviderTier.FREE_API,
                _etherscan_tx_trace,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "moralis_txns",
                ProviderTier.FREEMIUM,
                _moralis_wallet_transactions,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["wallet_net_worth"] = ProviderChain(
        data_type="wallet_net_worth",
        description="Wallet net worth across chains (Moralis)",
        providers=[
            Provider(
                "moralis_net_worth",
                ProviderTier.FREEMIUM,
                _moralis_wallet_net_worth,
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["token_search"] = ProviderChain(
        data_type="token_search",
        description="Search tokens by name/symbol/address (Moralis + MCP)",
        providers=[
            Provider(
                "moralis_search",
                ProviderTier.FREEMIUM,
                _moralis_search_tokens,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Arkham Intelligence ──
    chains["entity_intel"] = ProviderChain(
        data_type="entity_intel",
        description="Entity resolution and risk scoring (local labels → Arkham free → Moralis)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_entity",
                ProviderTier.FREEMIUM,
                _arkham_entity,
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
            Provider(
                "moralis_labels",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["arkham_labels"] = ProviderChain(
        data_type="arkham_labels",
        description="Entity labels with confidence scores (local → Arkham free trial)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_labels_api",
                ProviderTier.FREEMIUM,
                _arkham_labels,
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )
    chains["arkham_portfolio"] = ProviderChain(
        data_type="arkham_portfolio",
        description="Portfolio holdings with USD values (Arkham free trial)",
        providers=[
            Provider(
                "arkham_portfolio_api",
                ProviderTier.FREEMIUM,
                _arkham_portfolio,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )
    chains["arkham_transfers"] = ProviderChain(
        data_type="arkham_transfers",
        description="Transfer history with counterparty mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_transfers_api",
                ProviderTier.FREEMIUM,
                _arkham_transfers,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )
    chains["arkham_counterparties"] = ProviderChain(
        data_type="arkham_counterparties",
        description="Counterparty network and relationship mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_counterparties_api",
                ProviderTier.FREEMIUM,
                _arkham_counterparties,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )
    chains["arkham_intel"] = ProviderChain(
        data_type="arkham_intel",
        description="Entity intelligence search (Arkham free trial)",
        providers=[
            Provider(
                "arkham_intel_search",
                ProviderTier.FREEMIUM,
                _arkham_intel_search,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    # ── MCP Bridge ──
    chains["mcp_bridge"] = ProviderChain(
        data_type="mcp_bridge",
        description="Universal MCP bridge - calls any local/remote MCP server tool",
        providers=[
            Provider("mcp_bridge", ProviderTier.LOCAL, _mcp_bridge, weight=10.0),
        ],
    )

    # ── PREMIUM SCANNER ──
    try:
        from app.databus.premium_scanner import (
            detect_bot_farms,
            detect_bundles,
            detect_copy_trading,
            detect_fresh_wallets,
            detect_insider_signals,
            detect_mev_sandwich,
            detect_snipers,
            detect_wash_trading,
            find_dev_wallets,
            map_clusters,
        )

        def _premium_provider(name: str, fn: Any, rps: float = 3.0) -> Provider:
            return Provider(name, ProviderTier.LOCAL, fn, weight=10.0, rate_limit_rps=rps)

        chains["bundle_detect"] = ProviderChain(
            "bundle_detect",
            description="Coordinated wallet bundle detection (RugMaps-style cluster analysis)",
            providers=[_premium_provider("bundle_scanner", detect_bundles)],
        )
        chains["cluster_map"] = ProviderChain(
            "cluster_map",
            description="Full wallet cluster mapping - funders, recipients, counterparties (graph-ready)",
            providers=[_premium_provider("cluster_mapper", map_clusters, rps=1.0)],
        )
        chains["dev_finder"] = ProviderChain(
            "dev_finder",
            description="Find developer/creator wallets behind tokens (deployer→funder→team)",
            providers=[_premium_provider("dev_finder_scanner", find_dev_wallets)],
        )
        chains["sniper_detect"] = ProviderChain(
            "sniper_detect",
            description="Sniper detection - first-block buyers with fast dump patterns",
            providers=[_premium_provider("sniper_scanner", detect_snipers)],
        )
        chains["bot_farm_detect"] = ProviderChain(
            "bot_farm_detect",
            description="Bot farm detection - identical behavior patterns across wallets",
            providers=[_premium_provider("bot_farm_scanner", detect_bot_farms)],
        )
        chains["copy_trade_detect"] = ProviderChain(
            "copy_trade_detect",
            description="Copy trading pattern detection - wallets mirroring trades with delay",
            providers=[_premium_provider("copy_trade_scanner", detect_copy_trading)],
        )
        chains["insider_detect"] = ProviderChain(
            "insider_detect",
            description="Insider trading signals - large buys before major announcements",
            providers=[_premium_provider("insider_scanner", detect_insider_signals)],
        )
        chains["wash_trade_detect"] = ProviderChain(
            "wash_trade_detect",
            description="Wash trading detection - circular transactions, self-trading",
            providers=[_premium_provider("wash_trade_scanner", detect_wash_trading)],
        )
        chains["mev_detect"] = ProviderChain(
            "mev_detect",
            description="MEV sandwich attack detection - frontrun/backrun patterns",
            providers=[_premium_provider("mev_scanner", detect_mev_sandwich)],
        )
        chains["fresh_wallet_analysis"] = ProviderChain(
            "fresh_wallet_analysis",
            description="Fresh wallet concentration analysis - high new-wallet % = rug risk",
            providers=[_premium_provider("fresh_wallet_scanner", detect_fresh_wallets, rps=2.0)],
        )
        logger.info(
            "Premium scanner chains registered: bundle_detect, cluster_map, dev_finder, "
            "sniper_detect, bot_farm, copy_trade, insider, wash_trade, mev, fresh_wallets"
        )
    except ImportError as e:
        logger.warning(f"Premium scanner not available: {e}")

    # ── WEBHOOK SYSTEM ──
    try:
        from app.databus.webhooks import handle_webhook, list_webhooks, setup_webhook  # noqa: F401

        chains["webhook_handler"] = ProviderChain(
            "webhook_handler",
            description="Intelligent webhook receiver - Arkham, Helius, Moralis, Alchemy + custom",
            providers=[
                Provider(
                    "webhook_processor",
                    ProviderTier.LOCAL,
                    lambda **kw: handle_webhook(
                        kw.get("service", ""),
                        kw.get("payload", {}),
                        kw.get("headers", {}),
                        kw.get("raw_body", b""),
                    ),
                    weight=10.0,
                )
            ],
        )
    except ImportError:
        pass

    # ── ARKHAM WEBSOCKET ──
    try:
        from app.databus.arkham_ws import arkham_ws_subscribe

        chains["arkham_ws"] = ProviderChain(
            "arkham_ws",
            description="Arkham Intelligence WebSocket - real-time entity updates, transfers, labels",
            providers=[
                Provider(
                    "arkham_ws_stream",
                    ProviderTier.FREEMIUM,
                    arkham_ws_subscribe,
                    weight=10.0,
                    rate_limit_rps=10.0,
                    requires_key=True,
                    key_env="ARKHAM_WS_KEY",
                    monthly_quota=500000,
                )
            ],
        )
        logger.info("Arkham WebSocket chain registered")
    except ImportError:
        logger.info("Arkham WS module not found - skipping WS chain")

    # ── Volume Authenticity ──
    try:
        from app.databus.volume_authenticity import analyze_volume_authenticity

        chains["volume_authenticity"] = ProviderChain(
            "volume_authenticity",
            description="Fake volume detection - 4-layer analysis (statistical, graph, heuristic, ML) with bootstrap CI",
            providers=[
                Provider(
                    "volume_auth_scorer",
                    ProviderTier.LOCAL,
                    analyze_volume_authenticity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        logger.info("Volume Authenticity chain registered")
    except ImportError:
        logger.info("Volume Authenticity module not found")

    # ── OHLCV Engine ──
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv, ingest_trade_data

        chains["ohlcv"] = ProviderChain(
            "ohlcv",
            description="Real-time OHLCV candle aggregation (1m/5m/15m/1h/4h/1d) with authenticity scoring",
            providers=[
                Provider(
                    "ohlcv_fetcher",
                    ProviderTier.LOCAL,
                    fetch_ohlcv,
                    weight=10.0,
                    rate_limit_rps=20.0,
                ),
                Provider(
                    "ohlcv_ingest",
                    ProviderTier.LOCAL,
                    ingest_trade_data,
                    weight=5.0,
                    rate_limit_rps=50.0,
                ),
            ],
        )
        logger.info("OHLCV Engine chain registered")
    except ImportError:
        logger.info("OHLCV Engine module not found")

    # ── Token Security Matrix ──
    try:
        from app.databus.token_security import get_check_matrix_endpoint, run_full_scan

        chains["token_security"] = ProviderChain(
            "token_security",
            description="37+ security checks - GoPlus, honeypot, contract, liquidity, holders, deployer, tokenomics, rug pull",
            providers=[
                Provider(
                    "security_scanner",
                    ProviderTier.LOCAL,
                    run_full_scan,
                    weight=10.0,
                    rate_limit_rps=10.0,
                ),
                Provider(
                    "check_matrix",
                    ProviderTier.LOCAL,
                    get_check_matrix_endpoint,
                    weight=1.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )
        logger.info("Token Security Matrix chain registered (37+ checks)")
    except ImportError:
        logger.info("Token Security module not found")

    # ── RUGCHARTS INTELLIGENCE ──
    try:
        from app.databus.data_quality import enhanced_token_report, get_tier_comparison
        from app.databus.rugcharts_intel import (
            cross_chain_entity,
            developer_reputation,
            holder_health_score,
            insider_pattern_detector,
            liquidity_risk_monitor,
            rug_pattern_matcher,
            smart_money_feed,
            token_launch_scanner,
            whale_alert_stream,
        )

        chains["smart_money"] = ProviderChain(
            "smart_money",
            description="Smart money feed - what profitable wallets are buying right now",
            providers=[
                Provider(
                    "smart_money_tracker",
                    ProviderTier.LOCAL,
                    smart_money_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["whale_alerts"] = ProviderChain(
            "whale_alerts",
            description="Real-time whale transaction detection - large transfers across chains",
            providers=[
                Provider(
                    "whale_detector",
                    ProviderTier.LOCAL,
                    whale_alert_stream,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["token_launches"] = ProviderChain(
            "token_launches",
            description="New token launch scanner with instant risk scoring (by age)",
            providers=[
                Provider(
                    "launch_scanner",
                    ProviderTier.LOCAL,
                    token_launch_scanner,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["insider_detection"] = ProviderChain(
            "insider_detection",
            description="Pre-pump accumulation pattern detection - volume spikes before price moves",
            providers=[
                Provider(
                    "insider_detector",
                    ProviderTier.LOCAL,
                    insider_pattern_detector,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["liquidity_risk"] = ProviderChain(
            "liquidity_risk",
            description="LP health monitor - concentration, lock status, removal risk",
            providers=[
                Provider(
                    "liquidity_monitor",
                    ProviderTier.LOCAL,
                    liquidity_risk_monitor,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["holder_health"] = ProviderChain(
            "holder_health",
            description="Holder distribution analysis - Gini, concentration, decentralization score",
            providers=[
                Provider(
                    "holder_analyzer",
                    ProviderTier.LOCAL,
                    holder_health_score,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["cross_chain_entity"] = ProviderChain(
            "cross_chain_entity",
            description="Cross-chain entity resolution via Arkham - trace wallets across all chains",
            providers=[
                Provider(
                    "entity_tracer",
                    ProviderTier.LOCAL,
                    cross_chain_entity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["rug_patterns"] = ProviderChain(
            "rug_patterns",
            description="Rug pull pattern matcher - similarity scoring against 10 known scam patterns",
            providers=[
                Provider(
                    "rug_matcher",
                    ProviderTier.LOCAL,
                    rug_pattern_matcher,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["dev_reputation"] = ProviderChain(
            "dev_reputation",
            description="Developer reputation - deployer history, token count, entity resolution",
            providers=[
                Provider(
                    "dev_reputation",
                    ProviderTier.LOCAL,
                    developer_reputation,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["token_report"] = ProviderChain(
            "token_report",
            description="ONE-CALL enhanced token report - smart verdicts, entity enrichment, trust adjustments, tier-aware",
            providers=[
                Provider(
                    "report_generator",
                    ProviderTier.LOCAL,
                    enhanced_token_report,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )
        chains["tier_comparison"] = ProviderChain(
            "tier_comparison",
            description="Competitive tier comparison - RugCharts vs DexScreener vs Nansen vs GMGN",
            providers=[
                Provider(
                    "tier_compare",
                    ProviderTier.LOCAL,
                    get_tier_comparison,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )
        logger.info("RugCharts Intelligence: 10 premium chains registered")
    except ImportError as e:
        logger.warning(f"RugCharts Intelligence not available: {e}")

    # ── NEWS & MARKET DATA ──
    try:
        from app.databus.news_provider import (
            get_fear_greed,
            get_full_news_feed,
            get_market_brief,
            get_market_prices,
            get_prediction_markets,
            get_trending_coins,
        )

        chains["live_prices"] = ProviderChain(
            "live_prices",
            description="Live crypto prices - CoinGecko free tier, multi-coin",
            providers=[
                Provider(
                    "coingecko_prices",
                    ProviderTier.LOCAL,
                    get_market_prices,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["trending_coins"] = ProviderChain(
            "trending_coins",
            description="Trending coins - CoinGecko search, top 10",
            providers=[
                Provider(
                    "coingecko_trending",
                    ProviderTier.LOCAL,
                    get_trending_coins,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["fear_greed"] = ProviderChain(
            "fear_greed",
            description="Crypto Fear & Greed Index - Alternative.me, free, no key",
            providers=[
                Provider(
                    "fear_greed_index",
                    ProviderTier.LOCAL,
                    get_fear_greed,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["prediction_markets"] = ProviderChain(
            "prediction_markets",
            description="Prediction market events - Polymarket, free, no key",
            providers=[
                Provider(
                    "polymarket_events",
                    ProviderTier.LOCAL,
                    get_prediction_markets,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["market_brief"] = ProviderChain(
            "market_brief",
            description="One-call market overview: prices + fear/greed + trending + prediction markets",
            providers=[
                Provider(
                    "market_briefing",
                    ProviderTier.LOCAL,
                    get_market_brief,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["full_news"] = ProviderChain(
            "full_news",
            description="Complete news feed: headlines + market data + fear/greed + polymarket predictions",
            providers=[
                Provider(
                    "full_news_feed",
                    ProviderTier.LOCAL,
                    get_full_news_feed,
                    weight=15.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        logger.info(
            "News & Market Data chains registered (6: prices, trending, fear_greed, "
            "prediction_markets, market_brief, full_news)"
        )
    except ImportError as e:
        logger.warning(f"News providers not available: {e}")

    # ── NEWS INTELLIGENCE ENGINE ──
    try:
        from app.databus.news_intel import (
            add_comment,
            add_reaction,
            aggregate_all_news,
            create_bb_post,
            get_academic_papers,
            get_reactions,
            get_social_feed,
            get_weekly_best,
        )

        chains["news_intel"] = ProviderChain(
            "news_intel",
            description="Complete news intelligence - 10+ sources, quality-scored, deduped, sentiment-tagged",
            providers=[
                Provider(
                    "news_engine",
                    ProviderTier.LOCAL,
                    aggregate_all_news,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )
        chains["weekly_best"] = ProviderChain(
            "weekly_best",
            description="Curated weekly best - highest quality crypto journalism",
            providers=[
                Provider(
                    "weekly_curator",
                    ProviderTier.LOCAL,
                    get_weekly_best,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["academic_papers"] = ProviderChain(
            "academic_papers",
            description="Academic crypto/blockchain research papers from arXiv",
            providers=[
                Provider(
                    "arxiv_fetcher",
                    ProviderTier.LOCAL,
                    get_academic_papers,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["social_feed"] = ProviderChain(
            "social_feed",
            description="Crypto social feed - X/Twitter + CryptoPanic sentiment",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["article_reactions"] = ProviderChain(
            "article_reactions",
            description="Article reactions - 🔥🐂🐻💎🧠🤡🚀💀 with counts",
            providers=[
                Provider(
                    "react_article",
                    ProviderTier.LOCAL,
                    add_reaction,
                    weight=5.0,
                    rate_limit_rps=30.0,
                ),
                Provider(
                    "get_reactions",
                    ProviderTier.LOCAL,
                    get_reactions,
                    weight=5.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )
        chains["article_comments"] = ProviderChain(
            "article_comments",
            description="Article comments - community discussion on any story",
            providers=[
                Provider(
                    "comment_article",
                    ProviderTier.LOCAL,
                    add_comment,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )
        chains["bb_post"] = ProviderChain(
            "bb_post",
            description="Convert article to Bulletin Board post for community engagement",
            providers=[
                Provider(
                    "create_bb_post",
                    ProviderTier.LOCAL,
                    create_bb_post,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        logger.info(
            "News Intelligence chains registered (7: news_intel, weekly_best, academic_papers, "
            "social_feed, reactions, comments, bb_post)"
        )
    except ImportError as e:
        logger.warning(f"News Intelligence not available: {e}")

    # ── X/CT INTELLIGENCE ──
    try:
        from app.databus.x_intel import fetch_ct_rundown, track_ct_accounts

        chains["ct_rundown"] = ProviderChain(
            "ct_rundown",
            description="CT Rundown - top 20 Crypto Twitter stories, AI-summarized, category-diverse",
            providers=[
                Provider(
                    "ct_scanner",
                    ProviderTier.LOCAL,
                    fetch_ct_rundown,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )
        chains["ct_accounts"] = ProviderChain(
            "ct_accounts",
            description="Curated CT account list - 35+ top accounts across 5 tiers",
            providers=[
                Provider(
                    "ct_account_list",
                    ProviderTier.LOCAL,
                    track_ct_accounts,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )
        logger.info("X/CT Intelligence chains registered (2: ct_rundown, ct_accounts)")
    except ImportError as e:
        logger.warning(f"X/CT Intelligence not available: {e}")

    # ── SOCIAL INTELLIGENCE ──
    try:
        from app.databus.daily_intel import generate_daily_intel
        from app.databus.social_intel import (
            detect_shill_campaigns,
            get_kol_leaderboard,
            get_kol_profile,
            get_shill_alerts,
            get_social_metrics,
            scan_scam_channels,
            track_kol_call,
        )

        chains["kol_track"] = ProviderChain(
            "kol_track",
            description="KOL call tracking - record and analyze influencer token calls",
            providers=[
                Provider(
                    "kol_call_tracker",
                    ProviderTier.LOCAL,
                    track_kol_call,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )
        chains["kol_profile"] = ProviderChain(
            "kol_profile",
            description="KOL performance profile - trust score, call history, win rate",
            providers=[
                Provider(
                    "kol_profiler",
                    ProviderTier.LOCAL,
                    get_kol_profile,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )
        chains["kol_leaderboard"] = ProviderChain(
            "kol_leaderboard",
            description="KOL leaderboard - ranked by trust score and accuracy",
            providers=[
                Provider(
                    "kol_board",
                    ProviderTier.LOCAL,
                    get_kol_leaderboard,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["shill_detector"] = ProviderChain(
            "shill_detector",
            description="Shill campaign detection - coordinated promotion, paid content, pump-and-dump",
            providers=[
                Provider(
                    "shill_scanner",
                    ProviderTier.LOCAL,
                    detect_shill_campaigns,
                    weight=10.0,
                    rate_limit_rps=5.0,
                ),
                Provider(
                    "shill_alerts",
                    ProviderTier.LOCAL,
                    get_shill_alerts,
                    weight=5.0,
                    rate_limit_rps=20.0,
                ),
            ],
        )
        chains["scam_monitor"] = ProviderChain(
            "scam_monitor",
            description="Scam channel monitor - Telegram/Discord scam pattern detection",
            providers=[
                Provider(
                    "scam_scanner",
                    ProviderTier.LOCAL,
                    scan_scam_channels,
                    weight=5.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["daily_intel"] = ProviderChain(
            "daily_intel",
            description="Daily Intelligence Briefing - OpenRouter free model research + writing, publish to X/Telegram/Ghost",
            providers=[
                Provider(
                    "intel_reporter",
                    ProviderTier.LOCAL,
                    generate_daily_intel,
                    weight=15.0,
                    rate_limit_rps=1.0,
                )
            ],
        )
        chains["social_metrics"] = ProviderChain(
            "social_metrics",
            description="Social metrics aggregator - trending topics, sentiment, KOL activity",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_metrics,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        logger.info(
            "Social Intelligence chains registered (8: kol_track, kol_profile, kol_leaderboard, "
            "shill_detector, scam_monitor, daily_intel, social_metrics)"
        )
    except ImportError as e:
        logger.warning(f"Social Intelligence not available: {e}")

    # ── MODEL REGISTRY ──
    try:
        from app.databus.model_registry import ai_call, get_usage_stats, review_content

        chains["ai_task"] = ProviderChain(
            "ai_task",
            description="AI task execution - smart routing across free models (research/writing/coding/review/fast)",
            providers=[
                Provider(
                    "ai_runner",
                    ProviderTier.LOCAL,
                    lambda **kw: ai_call(
                        kw.get("task_type", "fast"),
                        kw.get("system", ""),
                        kw.get("user", ""),
                        kw.get("max_tokens", 1000),
                        kw.get("temperature", 0.5),
                    ),
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        chains["content_review"] = ProviderChain(
            "content_review",
            description="Content quality review - checks for AI-slop, forbidden words, human voice",
            providers=[
                Provider(
                    "quality_reviewer",
                    ProviderTier.LOCAL,
                    review_content,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )
        chains["ai_usage"] = ProviderChain(
            "ai_usage",
            description="AI model usage statistics - track free tier consumption",
            providers=[
                Provider(
                    "usage_tracker",
                    ProviderTier.LOCAL,
                    get_usage_stats,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )
        logger.info("Model Registry chains registered (3: ai_task, content_review, ai_usage)")
    except ImportError as e:
        logger.warning(f"Model Registry not available: {e}")

    # ── RAG INGESTION ──
    try:
        from app.databus.rag_ingestion import nightly_rag_index, rag_health_check

        chains["rag_nightly"] = ProviderChain(
            "rag_nightly",
            description="Nightly RAG indexing - embeds news, CT, market, social data into vector store",
            providers=[
                Provider(
                    "rag_indexer",
                    ProviderTier.LOCAL,
                    nightly_rag_index,
                    weight=10.0,
                    rate_limit_rps=1.0,
                )
            ],
        )
        chains["rag_health"] = ProviderChain(
            "rag_health",
            description="RAG system health - collection stats, doc counts, embedder status",
            providers=[
                Provider(
                    "rag_checker",
                    ProviderTier.LOCAL,
                    rag_health_check,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )
        logger.info("RAG Ingestion chains registered (2: rag_nightly, rag_health)")
    except ImportError as e:
        logger.warning(f"RAG Ingestion not available: {e}")

    # ── HYPERLIQUID ──

    chains["hyperliquid"] = ProviderChain(
        data_type="hyperliquid",
        description="Hyperliquid perp markets and funding rates",
        providers=[
            Provider("rmi_hyperliquid", ProviderTier.LOCAL, _passthrough_hyperliquid, weight=10.0),
        ],
    )

    # ── HYPERLIQUID ACTION ──

    chains["hyperliquid_action"] = ProviderChain(
        data_type="hyperliquid_action",
        description="Hyperliquid gain/loss porn: top movers, funding squeezes, highest volume",
        providers=[
            Provider(
                "rmi_hyperliquid_action",
                ProviderTier.LOCAL,
                _passthrough_hyperliquid_action,
                weight=10.0,
            ),
        ],
    )

    # ── INSIDER WALLETS ──

    chains["insider_wallets"] = ProviderChain(
        data_type="insider_wallets",
        description="Likely insider trader wallets to watch (Premium)",
        providers=[
            Provider(
                "rmi_insider_wallets",
                ProviderTier.LOCAL,
                _passthrough_insider_wallets,
                weight=10.0,
            ),
        ],
    )

    # ── PREDICTION SIGNALS ──

    chains["prediction_signals"] = ProviderChain(
        data_type="prediction_signals",
        description="Prediction market intelligence signals (Polymarket, Kalshi, etc.)",
        providers=[
            Provider(
                "rmi_prediction_signals",
                ProviderTier.LOCAL,
                _passthrough_prediction_signals,
                weight=10.0,
            ),
        ],
    )

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
