"""Canonical chain-name normalisation.

Single source of truth for mapping provider/UI chain aliases (``eth``,
``bnb``, ``xdai``, ...) onto the canonical chain ids used by ClickHouse
tables, provider APIs and the scanner engines.

Before this module the same 20-entry map was copy-pasted into nineteen
engine/provider modules, so a new alias had to be added nineteen times and
the copies had silently drifted (17/18/19/20 entries).
"""

from __future__ import annotations

CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "solana": "solana",
}


def normalize_chain(chain: str) -> str:
    """Map a chain alias onto its canonical id (identity when unknown)."""
    return CHAIN_MAP.get(chain.lower(), chain.lower())
