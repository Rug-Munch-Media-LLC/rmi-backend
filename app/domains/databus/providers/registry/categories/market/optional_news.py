"""
Market provider chains — optional news & OHLCV providers.

Chains: ohlcv, live_prices, trending_coins, fear_greed, prediction_markets,
market_brief. These wrap ``app.databus.ohlcv_engine`` /
``app.databus.news_provider``, which are optional modules: when one is not
installed its chains are still registered with a ``None`` fetch function so
the rest of the market registry keeps building.
Split from ``market/market_data.py``.
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def _ohlcv_providers() -> tuple[Any, Any]:
    """Return the optional ohlcv_engine entrypoints, or (None, None).

    These live in a non-mandatory databus module; when it is absent the
    chains are still registered (as before) with a None fetch function.
    """
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv, ingest_trade_data
    except ImportError:
        return None, None
    return fetch_ohlcv, ingest_trade_data


def _news_providers() -> tuple[Any, Any, Any, Any, Any]:
    """Return the optional news_provider entrypoints, or five Nones.

    Same contract as :func:`_ohlcv_providers` — missing module must not stop
    the remaining market chains from being registered.
    """
    try:
        from app.databus.news_provider import (
            get_fear_greed,
            get_market_brief,
            get_market_prices,
            get_prediction_markets,
            get_trending_coins,
        )
    except ImportError:
        return None, None, None, None, None
    return get_fear_greed, get_market_brief, get_market_prices, get_prediction_markets, get_trending_coins


def _ohlcv_chain(fetch_ohlcv: Any, ingest_trade_data: Any) -> ProviderChain:
    """Real-time OHLCV candle aggregation with authenticity scoring."""
    return ProviderChain(
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


def _single_provider_chain(
    data_type: str,
    provider_name: str,
    fetch: Any,
    rate_limit_rps: float,
) -> ProviderChain:
    """One-LOCAL-provider chain — the shape every news/sentiment chain uses.

    Keeps the four single-provider chains declarative instead of repeating
    nine lines of identical Provider construction each.
    """
    return ProviderChain(
        data_type,
        description=_SINGLE_CHAIN_DESCRIPTIONS[data_type],
        providers=[
            Provider(
                provider_name,
                ProviderTier.LOCAL,
                fetch,
                weight=10.0,
                rate_limit_rps=rate_limit_rps,
            )
        ],
    )


_SINGLE_CHAIN_DESCRIPTIONS: dict[str, str] = {
    "live_prices": "Live crypto prices - CoinGecko free tier, multi-coin",
    "trending_coins": "Trending coins - CoinGecko search, top 10",
    "fear_greed": "Crypto Fear & Greed Index - Alternative.me, free, no key",
    "prediction_markets": "Prediction market events - Polymarket, free, no key",
    "market_brief": "One-call market overview: prices + fear/greed + trending + prediction markets",
}


def build_optional_news_chains() -> dict[str, ProviderChain]:
    """Optional news/OHLCV chains (ohlcv, prices, fear/greed, brief).

    Registration order is pinned by the market chain-order golden test:
    ``ohlcv``, ``live_prices``, ``trending_coins``, ``fear_greed``,
    ``prediction_markets``, ``market_brief``.
    """
    fetch_ohlcv, ingest_trade_data = _ohlcv_providers()
    get_fear_greed, get_market_brief, get_market_prices, get_prediction_markets, get_trending_coins = _news_providers()
    return {
        "ohlcv": _ohlcv_chain(fetch_ohlcv, ingest_trade_data),
        "live_prices": _single_provider_chain("live_prices", "coingecko_prices", get_market_prices, 10.0),
        "trending_coins": _single_provider_chain("trending_coins", "coingecko_trending", get_trending_coins, 5.0),
        "fear_greed": _single_provider_chain("fear_greed", "fear_greed_index", get_fear_greed, 10.0),
        "prediction_markets": _single_provider_chain(
            "prediction_markets", "polymarket_events", get_prediction_markets, 5.0
        ),
        "market_brief": _single_provider_chain("market_brief", "market_briefing", get_market_brief, 5.0),
    }
