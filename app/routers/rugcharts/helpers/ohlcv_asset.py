"""Resolve a pool's base token so asset-scoped chart sources can apply to pools.

Asset sources (Binance klines, CoinGecko OHLC) key on the TOKEN, not the pool.
GeckoTerminal publishes the base token, but it rate-limits (30 req/min public),
so DexScreener's pair payload is the second, independent resolver.
"""

from __future__ import annotations

from .common import logger
from .ohlcv import _fetch_pair_info
from .ohlcv_dexscreener import DEXSCREENER_CHAINS, DEXSCREENER_PAIRS_URL
from .ohlcv_source_http import fetch_json


async def _from_dexscreener(pair_address: str, chain: str) -> str:
    """Base token address from DexScreener's live pair payload ('' if absent)."""
    dex_chain = DEXSCREENER_CHAINS.get(chain, chain)
    payload, reason = await fetch_json(DEXSCREENER_PAIRS_URL.format(chain=dex_chain, pair=pair_address))
    if reason is not None or not isinstance(payload, dict):
        return ""
    pairs = payload.get("pairs") or []
    if not pairs or not isinstance(pairs[0], dict):
        return ""
    base = pairs[0].get("baseToken") or {}
    return str(base.get("address") or "")


async def resolve_base_asset(pair_address: str, chain: str) -> str:
    """Return the pool's base token address, or the pair address as last resort."""
    try:
        info = await _fetch_pair_info(pair_address, chain)
        base = str(info.get("base_token_address") or "")
    except Exception as exc:
        logger.debug("base-asset resolution via GeckoTerminal failed: %s", str(exc)[:100])
        base = ""
    if not base:
        base = await _from_dexscreener(pair_address, chain)
    return base or pair_address
