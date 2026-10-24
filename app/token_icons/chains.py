"""Chain slug mapping for the icon sources (single source of truth)."""

from __future__ import annotations

#: canonical chain -> (dexscreener chainId, coingecko asset platform)
_SLUGS: dict[str, tuple[str, str]] = {
    "solana": ("solana", ""),  # CoinGecko has no contract lookup for solana
    "ethereum": ("ethereum", "ethereum"),
    "eth": ("ethereum", "ethereum"),
    "bsc": ("bsc", "binance-smart-chain"),
    "bnb": ("bsc", "binance-smart-chain"),
    "base": ("base", "base"),
    "arbitrum": ("arbitrum", "arbitrum-one"),
    "polygon": ("polygon", "polygon-pos"),
    "tron": ("tron", "tron"),
    "avalanche": ("avalanche", "avalanche"),
    "optimism": ("optimism", "optimistic-ethereum"),
}


def chain_slugs(chain: str) -> tuple[str, str]:
    """Return ``(dexscreener_chain_id, coingecko_platform)``; ``("", "")`` if unknown."""
    return _SLUGS.get(str(chain or "").strip().lower(), ("", ""))
