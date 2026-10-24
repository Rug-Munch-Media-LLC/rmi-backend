"""Trust Wallet assets — free, static, GitHub-hosted token logos."""

from __future__ import annotations

from typing import Any

from eth_utils import to_checksum_address

_BASE = "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains"
_PATHS = {
    "ethereum": "ethereum/assets/{address}/logo.png",
    "eth": "ethereum/assets/{address}/logo.png",
    "bsc": "smartchain/assets/{address}/logo.png",
    "bnb": "smartchain/assets/{address}/logo.png",
    "polygon": "polygon/assets/{address}/logo.png",
    "arbitrum": "arbitrum/assets/{address}/logo.png",
    "base": "base/assets/{address}/logo.png",
    "optimism": "optimism/assets/{address}/logo.png",
    "avalanche": "avalanche/assets/{address}/logo.png",
    "solana": "solana/assets/{address}/logo.png",
    "tron": "tron/assets/{address}/logo.png",
}


def _address(chain: str, token: str) -> str | None:
    if chain in {"solana", "tron"}:
        return token
    try:
        return to_checksum_address(token)
    except (ValueError, TypeError):
        return None


async def fetch_icon(client: Any, chain: str, token: str) -> str | None:
    """Return the Trust Wallet logo URL, or None when this chain/path has none."""
    key = str(chain).strip().lower()
    path = _PATHS.get(key)
    if not path:
        return None
    address = _address(key, token)
    if not address:
        return None
    candidate = f"{_BASE}/{path.format(address=address)}"
    response = await client.head(candidate, timeout=6.0, follow_redirects=True)
    if response.status_code == 200:
        return candidate
    return None
