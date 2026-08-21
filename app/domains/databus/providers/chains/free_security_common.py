"""
Shared chain/network normalization tables for the free fallback providers.

Split out of ``free_security.py`` (degodding phase 3, 2026-08-21). The
tables map our canonical chain names to the per-API identifiers used by
the provider modules:

  - ``EVM_CHAIN_IDS``         — GoPlus, Honeypot.is, ParaSwap, 1inch
  - ``GECKOTERMINAL_NETWORKS`` — GeckoTerminal network slugs
  - ``COINGECKO_PLATFORMS``   — CoinGecko simple/token_price platform ids
  - ``BLOCKSCOUT_URLS``       — Blockscout v2 API base URLs
  - ``ETHERSCAN_V2_CHAIN_IDS`` — Etherscan-family V2 chain ids

All tables were verified live on 2026-08-02 (see the original
``free_security.py`` header for the per-endpoint verification notes).
"""

from __future__ import annotations

# ── Chain / network normalization tables ─────────────────────────────────
# EVM numeric chain IDs (GoPlus, Honeypot.is, Etherscan V2 use these)
EVM_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "mainnet": 1,
    "bsc": 56,
    "bnb": 56,
    "binance": 56,
    "polygon": 137,
    "matic": 137,
    "arbitrum": 42161,
    "arb": 42161,
    "optimism": 10,
    "op": 10,
    "base": 8453,
    "avalanche": 43114,
    "avax": 43114,
    "fantom": 250,
    "ftm": 250,
    "linea": 59144,
    "zksync": 324,
    "gnosis": 100,
    "xdai": 100,
    "scroll": 534352,
    "mantle": 5000,
}

# GeckoTerminal network slugs
GECKOTERMINAL_NETWORKS: dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon_pos",
    "matic": "polygon_pos",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avax",
    "avax": "avax",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "zksync": "zksync",
    "gnosis": "gnosis",
    "scroll": "scroll",
    "mantle": "mantle",
    "solana": "solana",
}

# CoinGecko platform IDs (simple/token_price/{platform_id})
COINGECKO_PLATFORMS: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bsc": "binance-smart-chain",
    "bnb": "binance-smart-chain",
    "polygon": "polygon-pos",
    "matic": "polygon-pos",
    "arbitrum": "arbitrum-one",
    "arb": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "op": "optimistic-ethereum",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "zksync": "zksync-era",
    "gnosis": "xdai",
    "scroll": "scroll",
    "mantle": "mantle",
}

# Blockscout v2 API base URLs per chain
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "eth": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "arb": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "matic": "https://polygon.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "op": "https://optimism.blockscout.com",
    "linea": "https://linea.blockscout.com",
    "zksync": "https://zksync.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
    "bnb": "https://bsc.blockscout.com",
    "avalanche": "https://avalanche.blockscout.com",
    "avax": "https://avalanche.blockscout.com",
}

# Etherscan-family V2 chain IDs
ETHERSCAN_V2_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "bsc": 56,
    "bnb": 56,
    "polygon": 137,
    "matic": 137,
    "arbitrum": 42161,
    "arb": 42161,
    "optimism": 10,
    "op": 10,
    "base": 8453,
    "avalanche": 43114,
    "avax": 43114,
    "fantom": 250,
    "ftm": 250,
    "linea": 59144,
    "zksync": 324,
}


def _normalize_chain(chain: str) -> str:
    """Lowercase chain name; returns '' if unknown/empty."""
    return (chain or "").strip().lower()
