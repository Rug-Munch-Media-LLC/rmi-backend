"""
Free API fallback providers — token security, wallet labels, market prices.

Replaces the lost Arkham API access with a stack of verified free APIs.
Each provider returns ``dict | None`` and follows the DataBus provider
contract: return ``None`` to fall through to the next tier in the chain.

Chains built from these providers (registered in registry/categories/):
  - token_security_free  (security.py) — ClickHouse → GoPlus → Honeypot.is → RugCheck → DexScreener
  - wallet_labels_free   (wallet.py)   — ClickHouse → Blockscout → Etherscan → DexScreener → heuristics
  - market_price_free    (market.py)   — ClickHouse TWAP → DexScreener → CoinGecko → GeckoTerminal → quotes

Verification (2026-08-02): all "free, no key" endpoints below were tested
live and return HTTP 200 without an API key. The only exceptions:
  - 1inch now requires an API key (HTTP 401) → registered as FREEMIUM
  - Etherscan migrated to V2 (HTTP 401 without key) → registered as FREEMIUM

DEGODDING (phase 3, 2026-08-21): the 988-line god module was split by
provider concern into focused modules; this file is now a thin re-export
hub so every existing importer (registry lazy map, category builders,
``chains/free_apis.py``, scanner ``_price_adapters.py``, and tests that
patch ``chains.free_security.*``) keeps working unchanged:

  - ``free_security_common.py``   — chain/network normalization tables
  - ``free_security_tokens.py``   — token_security_free tiers (5 providers)
  - ``free_wallet_labels.py``     — wallet_labels_free tiers (3 providers)
  - ``free_market_price.py``      — market_price_free tiers (5 providers)

The ``httpx`` import is retained deliberately: tests patch
``chains.free_security.httpx.AsyncClient`` (which patches the shared
``httpx`` module attribute seen by every provider module above).
"""

from __future__ import annotations

import httpx  # noqa: F401 — patch target for tests (chains.free_security.httpx.AsyncClient)

from app.domains.databus.providers.chains.free_market_price import (
    _free_market_clickhouse_twap,
    _free_market_coingecko,
    _free_market_geckoterminal,
    _free_market_oneinch_quote,
    _free_market_paraswap_quote,
)
from app.domains.databus.providers.chains.free_security_common import (
    BLOCKSCOUT_URLS,
    COINGECKO_PLATFORMS,
    ETHERSCAN_V2_CHAIN_IDS,
    EVM_CHAIN_IDS,
    GECKOTERMINAL_NETWORKS,
    _normalize_chain,
)
from app.domains.databus.providers.chains.free_security_tokens import (
    _free_security_clickhouse,
    _free_security_dexscreener,
    _free_security_goplus,
    _free_security_honeypot_is,
    _free_security_rugcheck,
)
from app.domains.databus.providers.chains.free_wallet_labels import (
    _free_blockscout_labels,
    _free_etherscan_labels,
    _free_wallet_pattern_heuristics,
)

__all__ = [
    # Normalization tables + helper (free_security_common)
    "BLOCKSCOUT_URLS",
    "COINGECKO_PLATFORMS",
    "ETHERSCAN_V2_CHAIN_IDS",
    "EVM_CHAIN_IDS",
    "GECKOTERMINAL_NETWORKS",
    # wallet_labels_free tiers (free_wallet_labels)
    "_free_blockscout_labels",
    "_free_etherscan_labels",
    # market_price_free tiers (free_market_price)
    "_free_market_clickhouse_twap",
    "_free_market_coingecko",
    "_free_market_geckoterminal",
    "_free_market_oneinch_quote",
    "_free_market_paraswap_quote",
    # token_security_free tiers (free_security_tokens)
    "_free_security_clickhouse",
    "_free_security_dexscreener",
    "_free_security_goplus",
    "_free_security_honeypot_is",
    "_free_security_rugcheck",
    "_free_wallet_pattern_heuristics",  # wallet_labels_free (free_wallet_labels)
    "_normalize_chain",  # normalization helper (free_security_common)
]
