"""Frozen chain-ORDER golden for the market provider-chain facade.

The market ``market.py`` → ``market/`` split grouped chains by theme, which
silently reordered registration: ``birdeye_price``/``price_fallback`` moved
ahead of the discovery chains, and ``rag_search``/``market_movers``/
``defi_protocols``/``new_launches`` drifted relative to their neighbours.
Registration order decides chain precedence in ``registry.c``, so the facade
in ``market/__init__.py`` is pinned here to the pre-split order.

Golden captured from the pre-split 678-line ``market.py``. Patch-free in the
same way as the security goldens: every provider fetch function is a marker
callable injected through the builder's own ``locals_dict``.
"""

from __future__ import annotations

from typing import Any

# Frozen registration order of the pre-split build_market_chains.
GOLDEN_MARKET_CHAIN_ORDER = (
    "token_price",
    "token_meta",
    "market_price_free",
    "market_overview",
    "trending",
    "hype_score",
    "token_trades",
    "top_traders",
    "dune_early_buyers",
    "defillama_tvl",
    "defillama_chains",
    "blockchair_address",
    "blockchair_stats",
    "birdeye_overview",
    "birdeye_price",
    "price_fallback",
    "ohlcv",
    "live_prices",
    "trending_coins",
    "fear_greed",
    "prediction_markets",
    "market_brief",
    "rag_search",
    "market_movers",
    "dex_data",
    "defi_protocols",
    "token_detail",
    "new_launches",
)

# Every locals() name the market builders read off the registry frame —
# captured from every ``_g["…"]`` lookup in the pre-split ``market.py``.
REQUIRED_MARKET_LOCAL_KEYS = (
    "_birdeye_overview",
    "_birdeye_price",
    "_blockchair_address",
    "_blockchair_stats",
    "_coingecko_price",
    "_defillama_chains",
    "_defillama_tvl",
    "_dexscreener_price",
    "_dexscreener_token_metadata",
    "_dexscreener_top_traders",
    "_dexscreener_trades",
    "_dune_early_buyers",
    "_free_market_clickhouse_twap",
    "_free_market_coingecko",
    "_free_market_geckoterminal",
    "_free_market_oneinch_quote",
    "_free_market_paraswap_quote",
    "_gmgn_bonded_pairs",
    "_gmgn_new_pairs",
    "_gmgn_token_info",
    "_gmgn_trending",
    "_lite_chain_indexer_hype_score",
    "_local_token_price",
    "_moralis_price",
    "_passthrough_market_overview",
    "_passthrough_rag",
    "_passthrough_trending",
    "_solana_tracker_price",
    "_solana_tracker_token",
    "_solana_tracker_trending",
    "token_meta_local",
    "token_price_local",
)


def _locals_dict() -> dict[str, Any]:
    """Registry frame stand-in: every market builder key maps to a stub."""

    def _make(key: str):
        def _fetch(*_args: Any, **_kwargs: Any) -> None:
            return None

        _fetch.__name__ = key.lstrip("_")
        return _fetch

    return {key: _make(key) for key in REQUIRED_MARKET_LOCAL_KEYS}
