"""
Market provider chains.
"""

from __future__ import annotations

import contextlib
from typing import Any

from ...base import Provider, ProviderChain, ProviderTier


def build_market_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build market provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict
    # ── Conditional imports for optional databus modules ──
    with contextlib.suppress(ImportError):
        pass
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv, ingest_trade_data
    except ImportError:
        fetch_ohlcv = None
        ingest_trade_data = None
    try:
        from app.databus.news_provider import (
            get_fear_greed,
            get_market_brief,
            get_market_prices,
            get_prediction_markets,
            get_trending_coins,
        )
    except ImportError:
        get_fear_greed = None
        get_market_brief = None
        get_market_prices = None
        get_prediction_markets = None
        get_trending_coins = None

    chains["token_price"] = ProviderChain(
        data_type="token_price",
        description="Token price across DEXs and CEXs",
        providers=[
            Provider(
                "token_price_local",
                ProviderTier.LOCAL,
                _g["token_price_local"],
                weight=25.0,
                is_local=True,
                description="F2 price oracle — own dex_events + free APIs (Redis/ClickHouse cached)",
            ),
            Provider("local_price", ProviderTier.LOCAL, _g["_local_token_price"], weight=10.0),
            Provider(
                "solana_tracker",
                ProviderTier.FREEMIUM,
                _g["_solana_tracker_price"],
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener",
                ProviderTier.FREE_API,
                _g["_dexscreener_price"],
                weight=5.0,
                rate_limit_rps=2.0,
            ),
            Provider("coingecko", ProviderTier.FREE_API, _g["_coingecko_price"], weight=5.0),
            Provider(
                "moralis",
                ProviderTier.PAID,
                _g["_moralis_price"],
                weight=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
                monthly_quota=5000,
            ),
        ],
    )

    chains["token_meta"] = ProviderChain(
        data_type="token_meta",
        description="Token metadata — decimals, symbol, name, last known price",
        providers=[
            Provider(
                "token_meta_local",
                ProviderTier.LOCAL,
                _g["token_meta_local"],
                weight=10.0,
                is_local=True,
                description="F2 price oracle token_meta (ClickHouse token_meta + DexScreener decimals)",
            ),
        ],
    )

    # ── market_price_free — free multi-tier fallback (Arkham-quality price replacement) ──
    #   Tier 1: own ClickHouse DEX events (own TWAP, local)
    #   Tier 2: DexScreener (free, 300 req/min)
    #   Tier 3: CoinGecko (free, 10-30 req/min)
    #   Tier 4: GeckoTerminal (free, no auth)
    #   Tier 5: 1inch/ParaSwap quotes (ParaSwap free; 1inch freemium w/ key)
    chains["market_price_free"] = ProviderChain(
        data_type="market_price_free",
        description="Free market price fallback: ClickHouse TWAP → DexScreener → CoinGecko → GeckoTerminal → DEX quotes",
        providers=[
            Provider(
                "free_market_clickhouse_twap",
                ProviderTier.LOCAL,
                _g["_free_market_clickhouse_twap"],
                weight=20.0,
                is_local=True,
                description="Own ClickHouse dex_events TWAP via own_price_oracle — zero cost",
            ),
            Provider(
                "dexscreener_price_free",
                ProviderTier.FREE_API,
                _g["_dexscreener_price"],
                weight=10.0,
                rate_limit_rps=2.0,
                description="DexScreener latest/dex/tokens — live pair price (free)",
            ),
            Provider(
                "free_market_coingecko",
                ProviderTier.FREE_API,
                _g["_free_market_coingecko"],
                weight=6.0,
                rate_limit_rps=0.5,
                description="CoinGecko simple/token_price — free tier 10-30 req/min",
            ),
            Provider(
                "free_market_geckoterminal",
                ProviderTier.FREE_API,
                _g["_free_market_geckoterminal"],
                weight=5.0,
                rate_limit_rps=2.0,
                description="GeckoTerminal token price (free, no auth)",
            ),
            Provider(
                "free_market_paraswap_quote",
                ProviderTier.FREE_API,
                _g["_free_market_paraswap_quote"],
                weight=3.0,
                rate_limit_rps=2.0,
                description="ParaSwap prices — best-route DEX quote (free, no auth)",
            ),
            Provider(
                "free_market_oneinch_quote",
                ProviderTier.FREEMIUM,
                _g["_free_market_oneinch_quote"],
                weight=2.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="ONEINCH_API_KEY",
                description="1inch swap quote — freemium (API key required as of 2026)",
            ),
        ],
    )

    chains["market_overview"] = ProviderChain(
        data_type="market_overview",
        description="Global market cap, BTC/ETH/SOL prices, fear & greed",
        providers=[
            Provider(
                "rmi_market_overview",
                ProviderTier.LOCAL,
                _g["_passthrough_market_overview"],
                weight=10.0,
            ),
            Provider(
                "coingecko_global",
                ProviderTier.FREEMIUM,
                _g["_coingecko_price"],
                weight=5.0,
                requires_key=True,
                key_env="COINGECKO_API_KEY",
            ),
        ],
    )

    chains["trending"] = ProviderChain(
        data_type="trending",
        description="Trending tokens across chains",
        providers=[
            Provider("rmi_trending", ProviderTier.LOCAL, _g["_passthrough_trending"], weight=10.0),
            Provider(
                "solana_tracker_trending",
                ProviderTier.FREEMIUM,
                _g["_solana_tracker_trending"],
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener_trending",
                ProviderTier.FREE_API,
                _g["_dexscreener_price"],
                weight=5.0,
                rate_limit_rps=1.0,
            ),
        ],
    )

    # ── C5 — hype score (LOCAL tier: own ClickHouse + free trending) ──
    chains["hype_score"] = ProviderChain(
        data_type="hype_score",
        description="Composite token hype score (0-100) from free social signals + own on-chain velocity — CoinGecko/DexScreener trending, dex_events volume growth, evm_transfers holder growth, price oracle momentum",
        providers=[
            Provider(
                "lite_chain_indexer_hype_score",
                ProviderTier.LOCAL,
                _g["_lite_chain_indexer_hype_score"],
                weight=20.0,
                is_local=True,
                description="ClickHouse dex_events + evm_transfers + unified trending — weighted composite with 72h exponential decay",
            ),
        ],
    )

    chains["token_trades"] = ProviderChain(
        data_type="token_trades",
        description="Live token trades (buys/sells) from DexScreener",
        providers=[
            Provider(
                "dexscreener_trades",
                ProviderTier.FREE_API,
                _g["_dexscreener_trades"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    chains["top_traders"] = ProviderChain(
        data_type="top_traders",
        description="Top profitable traders and win rates for a token",
        providers=[
            Provider(
                "dexscreener_top_traders",
                ProviderTier.FREE_API,
                _g["_dexscreener_top_traders"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    chains["dune_early_buyers"] = ProviderChain(
        data_type="dune_early_buyers",
        description="First 5-minute buyers of a token (Dune Analytics, low-CU cached query)",
        providers=[
            Provider(
                "dune_early_buyers_api",
                ProviderTier.FREEMIUM,
                _g["_dune_early_buyers"],
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="DUNE_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # Bitquery-backed chains (holder_data, tx_trace, cross_chain) are
    # registered in _generated/chains/bitquery.py via build_bitquery_chains().

    chains["defillama_tvl"] = ProviderChain(
        data_type="defillama_tvl",
        description="Global DeFi TVL and top protocols (completely free)",
        providers=[
            Provider(
                "defillama_tvl_api",
                ProviderTier.FREE_API,
                _g["_defillama_tvl"],
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
                _g["_defillama_chains"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    chains["blockchair_address"] = ProviderChain(
        data_type="blockchair_address",
        description="Multi-chain address balance and tx count (BTC, ETH, SOL, etc.)",
        providers=[
            Provider(
                "blockchair_addr_api",
                ProviderTier.FREE_API,
                _g["_blockchair_address"],
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
                _g["_blockchair_stats"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    chains["birdeye_overview"] = ProviderChain(
        data_type="birdeye_overview",
        description="Token overview, liquidity, and holder stats (Solana/EVM)",
        providers=[
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _g["_birdeye_overview"],
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
                _g["_birdeye_price"],
                weight=5.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
            Provider(
                "dexscreener_price",
                ProviderTier.FREE_API,
                _g["_dexscreener_price"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

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

    # ── RAG Search ──
    chains["rag_search"] = ProviderChain(
        data_type="rag_search",
        description="RAG-based semantic search across intelligence data",
        providers=[
            Provider("local_rag", ProviderTier.LOCAL, _g["_passthrough_rag"], weight=10.0),
        ],
    )

    # ── Market movers (top gainers/losers) — local backend + trending feeds ──
    chains["market_movers"] = ProviderChain(
        data_type="market_movers",
        description="Top market movers — local market overview → trending feeds",
        providers=[
            Provider(
                "rmi_market_overview",
                ProviderTier.LOCAL,
                _g["_passthrough_market_overview"],
                weight=10.0,
            ),
            Provider(
                "rmi_trending",
                ProviderTier.LOCAL,
                _g["_passthrough_trending"],
                weight=8.0,
            ),
            Provider(
                "solana_tracker_trending",
                ProviderTier.FREEMIUM,
                _g["_solana_tracker_trending"],
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
        ],
    )

    # ── DEX pool data (liquidity depth, volume, price impact) ──
    chains["dex_data"] = ProviderChain(
        data_type="dex_data",
        description="DEX pool data — liquidity depth, volume, pairs (DexScreener → Birdeye)",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _g["_dexscreener_token_metadata"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "dexscreener_price",
                ProviderTier.FREE_API,
                _g["_dexscreener_price"],
                weight=8.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _g["_birdeye_overview"],
                weight=5.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
        ],
    )

    # ── DeFi protocol tracker (TVL, chains, categories, revenue) ──
    chains["defi_protocols"] = ProviderChain(
        data_type="defi_protocols",
        description="DeFi protocol tracker — TVL, chains, categories (DeFiLlama free)",
        providers=[
            Provider(
                "defillama_tvl_api",
                ProviderTier.FREE_API,
                _g["_defillama_tvl"],
                weight=10.0,
                rate_limit_rps=2.0,
            ),
            Provider(
                "defillama_chains_api",
                ProviderTier.FREE_API,
                _g["_defillama_chains"],
                weight=5.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Rich token detail (market cap, volume, liquidity, holders, risk) ──
    chains["token_detail"] = ProviderChain(
        data_type="token_detail",
        description="Full token intelligence — market cap, volume, liquidity, holders, risk flags",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _g["_dexscreener_token_metadata"],
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "solana_tracker_token",
                ProviderTier.FREEMIUM,
                _g["_solana_tracker_token"],
                weight=6.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _g["_birdeye_overview"],
                weight=4.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
            Provider(
                "gmgn_token_info",
                ProviderTier.FREE_API,
                _g["_gmgn_token_info"],
                weight=3.0,
                rate_limit_rps=1.0,
                description="GMGN public API — token overview, holders, launchpad progress (free, no key)",
            ),
        ],
    )

    # ── New token launch feed (external fallback — own data served by the route) ──
    chains["new_launches"] = ProviderChain(
        data_type="new_launches",
        description="New token launch feed (external tier) — GMGN pump.fun new_creation → GMGN bonded pairs → DexScreener boosts",
        providers=[
            Provider(
                "gmgn_new_pairs",
                ProviderTier.FREE_API,
                _g["_gmgn_new_pairs"],
                weight=6.0,
                rate_limit_rps=1.0,
                description="GMGN pump.fun new_creation rank — fresh launches (free, no key)",
            ),
            Provider(
                "gmgn_bonded_pairs",
                ProviderTier.FREE_API,
                _g["_gmgn_bonded_pairs"],
                weight=4.0,
                rate_limit_rps=1.0,
                description="GMGN pump.fun bonded pairs — bonding-curve-completed launches (free, no key)",
            ),
        ],
    )

    return chains
