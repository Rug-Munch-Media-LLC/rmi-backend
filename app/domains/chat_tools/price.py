"""Price executor for the chat tool-use layer.

Best-effort async executor that calls the DIRECT data modules (CoinGecko
connector / DexScreener) — never the x402 endpoints. Fail-open: returns
``None`` on any error/timeout and NEVER raises.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from ._shared import _to_float

logger = get_logger(__name__)

# Symbol → CoinGecko ID for the free /simple/price path (no key required).
_COINGECKO_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "bnb": "binancecoin",
    "ada": "cardano",
    "doge": "dogecoin",
    "avax": "avalanche-2",
    "dot": "polkadot",
    "matic": "matic-network",
    "polygon": "matic-network",
    "link": "chainlink",
    "shib": "shiba-inu",
    "pepe": "pepe",
    "usdt": "tether",
    "usdc": "usd-coin",
    "ltc": "litecoin",
    "atom": "cosmos",
    "trx": "tron",
    "ton": "the-open-network",
    "near": "near",
    "apt": "aptos",
    "arb": "arbitrum",
    "op": "optimism",
    "sui": "sui",
}


def _best_dex_pair(data: Any) -> dict[str, Any] | None:
    """Best DexScreener pair by USD liquidity (free, no key)."""
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None

    def _liq(p: Any) -> float:
        liq = p.get("liquidity") if isinstance(p, dict) else None
        return (_to_float(liq.get("usd")) or 0.0) if isinstance(liq, dict) else 0.0

    return max(pairs, key=_liq) if pairs else None


async def _exec_price(message: str) -> str | None:
    """Fetch a live token price. Returns a one-line summary or None."""
    # Patch-sensitive shared helpers resolve through the package shim at call
    # time so tests can mock them on app.domains.chat_tools.* (lazy to avoid
    # an import cycle — the shim imports this module).
    from app.domains.chat_tools import (
        _chain_for_address,
        _extract_first_address,
        _extract_price_symbol,
    )

    symbol = _extract_price_symbol(message)
    if not symbol:
        return None
    try:
        address = _extract_first_address(message)
        if address:
            chain = _chain_for_address(address)
            if chain == "solana":
                from app.databus.provider_implementations.price import _dexscreener_price

                pair = _best_dex_pair(await _dexscreener_price(token=address))
                if not pair:
                    return None
                price = _to_float(pair.get("priceUsd"))
                change = _to_float(pair.get("priceChange24h"))
            else:
                from app.coingecko_connector import get_coingecko_connector

                data = await get_coingecko_connector().get_token_price_by_contract("ethereum", address)
                if not data:
                    return None
                price = _to_float(data.get("usd"))
                change = _to_float(data.get("usd_24h_change"))
        else:
            from app.coingecko_connector import get_coingecko_connector

            cg_id = _COINGECKO_IDS.get(symbol.lower())
            if not cg_id:
                return None
            data = await get_coingecko_connector().get_token_price(cg_id)
            if not data:
                return None
            price = _to_float(data.get("usd"))
            change = _to_float(data.get("usd_24h_change"))

        if not price or price <= 0:
            return None
        change_str = f" (24h {change:+.1f}%)" if change is not None else ""
        return f"{symbol.upper()} is ${price:,.2f}{change_str}"
    except Exception as e:
        logger.debug("chat_tool_price_failed", error=str(e)[:120])
        return None
