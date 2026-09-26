"""
Market provider chains
======================

Market-domain provider chain builders, grouped into four focused modules
that each stay under the 200-line / 50-line-function limits:

===========================  ================================================
Module                       Chains
===========================  ================================================
``token_price``              token_price, token_meta, market_price_free
``price``                    birdeye_price, price_fallback
``market_data``              market_overview, trending, hype_score,
                             market_movers
``optional_news``            ohlcv, live_prices, trending_coins, fear_greed,
                             prediction_markets, market_brief
``discovery``                token_trades, top_traders, dune_early_buyers,
                             defillama_tvl, defillama_chains,
                             blockchair_address, blockchair_stats,
                             birdeye_overview
``token_intel``              rag_search, dex_data, defi_protocols,
                             token_detail, new_launches
===========================  ================================================

``build_market_chains`` keeps the original single-entry signature (it is
invoked as ``build_market_chains(locals())`` by the registry) and the
original registration order of every chain.

Bitquery-backed chains (holder_data, tx_trace, cross_chain) are registered
in ``_generated/chains/bitquery.py`` via ``build_bitquery_chains()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .defi_protocols import build_defi_protocols_chains
from .dex_data import build_dex_data_chains
from .discovery import build_discovery_chains
from .market_data import build_market_data_chains
from .market_movers import build_market_movers_chains
from .new_launches import build_new_launches_chains
from .optional_news import build_optional_news_chains
from .price import build_quote_chains
from .rag_search import build_rag_search_chains
from .token_intel import build_token_intel_chains
from .token_price import build_token_price_chains

if TYPE_CHECKING:
    from ....base import ProviderChain


def build_market_chains(locals_dict: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build market provider chains.

    ``locals_dict`` is the registry's own frame (``locals()``), which carries
    every provider function as a name — the builders read what they need from
    it and never import the chains directly.

    The ``chains.update()`` sequence below IS the registration order, and it
    is pinned by ``test_registry_market_chain_order.py``. Reordering these
    lines changes ``registry.c`` chain precedence, so keep it in step with the
    pre-split ``market.py`` order.
    """
    chains: dict[str, ProviderChain] = {}
    chains.update(build_token_price_chains(locals_dict))
    chains.update(build_market_data_chains(locals_dict))
    chains.update(build_discovery_chains(locals_dict))
    chains.update(build_quote_chains(locals_dict))
    chains.update(build_optional_news_chains())
    chains.update(build_rag_search_chains(locals_dict))
    chains.update(build_market_movers_chains(locals_dict))
    chains.update(build_dex_data_chains(locals_dict))
    chains.update(build_defi_protocols_chains(locals_dict))
    chains.update(build_token_intel_chains(locals_dict))
    chains.update(build_new_launches_chains(locals_dict))
    return chains


__all__ = [
    "build_defi_protocols_chains",
    "build_dex_data_chains",
    "build_discovery_chains",
    "build_market_chains",
    "build_market_data_chains",
    "build_market_movers_chains",
    "build_new_launches_chains",
    "build_optional_news_chains",
    "build_quote_chains",
    "build_rag_search_chains",
    "build_token_intel_chains",
    "build_token_price_chains",
]
