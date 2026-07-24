"""
Market provider chains.
"""

from __future__ import annotations

from ...base import Provider, ProviderChain, ProviderTier


def build_market_chains(locals_dict: dict) -> dict[str, ProviderChain]:
    """Build market provider chains."""
    chains: dict[str, ProviderChain] = {}
    _g = locals_dict
    # ── Conditional imports for optional databus modules ──
    try:
        from app.databus.bitquery_provider import BitqueryProvider
    except ImportError:
        BitqueryProvider = None
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

    return chains
