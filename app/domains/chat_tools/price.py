"""Price executor for the chat tool-use layer.

Best-effort async executor that calls the DIRECT data modules (CoinGecko
connector / DexScreener / CoinCap / CoinMarketCap) — never the x402
endpoints. Returns rich price context with market data. Fail-open: returns
``None`` on any error/timeout.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

from ._shared import _to_float

logger = logging.getLogger(__name__)

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
    "uni": "uniswap",
    "aave": "aave",
    "ldo": "lido-dao",
    "ondo": "ondo-finance",
}

# Symbol → CoinCap ID (free, 200 req/min, no key).
# CoinCap IDs are lowercase asset slugs (mostly similar to CoinGecko but
# not always identical — e.g. ``binance-coin`` vs ``binancecoin``).
_COINCAP_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "bnb": "binance-coin",
    "ada": "cardano",
    "doge": "dogecoin",
    "avax": "avalanche-w",
    "dot": "polkadot",
    "matic": "matic-network",
    "polygon": "matic-network",
    "shib": "shiba-inu",
    "pepe": "pepe",
    "usdt": "tether",
    "usdc": "usd-coin",
    "ltc": "litecoin",
    "atom": "cosmos",
    "trx": "tron",
    "ton": "toncoin",
    "near": "near",
    "apt": "aptos",
    "arb": "arbitrum",
    "op": "optimism",
    "sui": "sui",
    "uni": "uniswap",
    "aave": "aave",
    "ldo": "lido-dao",
    "ondo": "ondo-finance",
}


# CoinMarketCap uses uppercase ticker symbols (BTC, ETH, SOL, …).
# This is a simple identity mapping from lowercase → uppercase.
def _cmc_symbol(symbol: str) -> str:
    """Return the uppercase CMC ticker for *symbol*."""
    return symbol.upper()


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


async def _fetch_rich_cg_data(symbol: str, cg_id: str) -> dict[str, Any] | None:
    """Fetch enriched CoinGecko data including market cap, volume, supply info."""
    from app.coingecko_connector import get_coingecko_connector

    cg = get_coingecko_connector()
    data = await cg.get_token_price(cg_id)
    if not data:
        return None

    # Try to get extra market data
    extra = {}
    try:
        extra_data = await cg.get_coin_market_data(cg_id)
        if extra_data:
            extra = extra_data
    except Exception as exc:
        logger.debug("coingecko market-data enrichment failed: %s", exc)

    price = _to_float(data.get("usd"))
    change = _to_float(data.get("usd_24h_change"))
    market_cap = _to_float(data.get("usd_market_cap")) or _to_float(extra.get("market_cap", {}).get("usd"))
    volume_24h = (
        _to_float(data.get("usd_24h_vol"))
        or _to_float(data.get("total_volume"))
        or _to_float(extra.get("total_volume", {}).get("usd"))
    )
    high_24h = _to_float(extra.get("high_24h", {}).get("usd"))
    low_24h = _to_float(extra.get("low_24h", {}).get("usd"))
    dominance = _to_float(extra.get("market_cap_dominance"))
    circ_supply = _to_float(extra.get("circulating_supply"))

    return {
        "symbol": symbol.upper(),
        "price": price,
        "change_24h": change,
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "dominance": dominance,
        "circulating_supply": circ_supply,
        "source": "coingecko",
    }


async def _fetch_coincap_price(symbol: str) -> dict[str, Any] | None:
    """Fetch price from CoinCap free API (200 req/min, no key).

    CoinCap IDs are lowercase slugs.  Returns a dict with the same keys as
    ``_fetch_rich_cg_data`` but with source ``"coincap"``.
    """
    cap_id = _COINCAP_IDS.get(symbol.lower())
    if not cap_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://api.coincap.io/v2/assets/{cap_id}")
            if resp.status_code != 200:
                return None
            body = resp.json()
            data = body.get("data")
            if not data or not isinstance(data, dict):
                return None

            price = _to_float(data.get("priceUsd"))
            if not price:
                return None

            return {
                "symbol": symbol.upper(),
                "price": price,
                "change_24h": _to_float(data.get("changePercent24Hr")),
                "market_cap": _to_float(data.get("marketCapUsd")),
                "volume_24h": _to_float(data.get("volumeUsd24Hr")),
                "circulating_supply": _to_float(data.get("supply")),
                "source": "coincap",
            }
    except Exception as exc:
        logger.debug("chat_tool_price_coincap_failed", symbol=symbol, error=str(exc)[:120])
        return None


async def _fetch_coincap_price_by_address(address: str, chain: str) -> dict[str, Any] | None:
    """Fallback address lookup via CoinCap.

    CoinCap does **not** support contract-address lookups natively.  For
    Solana addresses we attempt to derive the token symbol from the
    DexScreener pair data (already fetched upstream) and then look it up by
    symbol.  For EVM addresses we try CoinGecko first in the caller, so
    this is a last-resort symbol-only path.
    """
    # CoinCap has no contract-address endpoint — this function exists
    # as a placeholder in the fallback chain.  Return None so the caller
    # moves to the next fallback (CMC) instead.
    return None


async def _fetch_cmc_price(symbol: str) -> dict[str, Any] | None:
    """Fetch price from CoinMarketCap free tier.

    Requires the ``CMC_API_KEY`` environment variable.  If unset or empty
    this function silently returns ``None``.  Returns the same shape as
    ``_fetch_rich_cg_data`` with source ``"coinmarketcap"``.
    """
    api_key = os.getenv("CMC_API_KEY", "")
    if not api_key:
        return None

    ticker = _cmc_symbol(symbol)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
                params={"symbol": ticker},
                headers={"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            if body.get("status", {}).get("error_code") != 0:
                return None

            quote_data = body.get("data", {}).get(ticker)
            if not quote_data:
                return None
            usd = quote_data.get("quote", {}).get("USD")
            if not usd:
                return None

            price = _to_float(usd.get("price"))
            if not price:
                return None

            return {
                "symbol": symbol.upper(),
                "price": price,
                "change_24h": _to_float(usd.get("percent_change_24h")),
                "market_cap": _to_float(usd.get("market_cap")),
                "volume_24h": _to_float(usd.get("volume_24h")),
                "circulating_supply": _to_float(usd.get("circulating_supply")),
                "source": "coinmarketcap",
            }
    except Exception as exc:
        logger.debug("chat_tool_price_cmc_failed", symbol=symbol, error=str(exc)[:120])
        return None


async def _fetch_price_with_fallback(
    symbol: str,
    address: str | None = None,
    chain: str = "",
) -> dict[str, Any] | None:
    """Try price providers in order: CoinGecko → CoinCap → CoinMarketCap.

    All functions are fail-open (return None on error).  Returns the first
    successful result, or ``None`` if every provider in the chain fails.
    """
    # --- Address-based path ---
    if address:
        if chain == "solana":
            from app.databus.provider_implementations.price import _dexscreener_price

            try:
                pair = _best_dex_pair(await _dexscreener_price(token=address))
                if pair:
                    price = _to_float(pair.get("priceUsd"))
                    change = _to_float(pair.get("priceChange24h"))
                    liq = (
                        _to_float(pair.get("liquidity", {}).get("usd"))
                        if isinstance(pair.get("liquidity"), dict)
                        else None
                    )
                    vol = _to_float(pair.get("volume", {}).get("h24")) if isinstance(pair.get("volume"), dict) else None
                    fdv = _to_float(pair.get("fdv"))
                    txns_24h = pair.get("txns", {}).get("h24", {})
                    buys = _to_float(txns_24h.get("buys"))
                    sells = _to_float(txns_24h.get("sells"))
                    pair_label = str(pair.get("baseToken", {}).get("symbol", symbol.upper()))

                    lines = [
                        f"**${pair_label}** `{address[:18]}…` on **Solana**",
                        "",
                        "| Metric | Value |",
                        "|--------|-------|",
                        f"| Price | ${price:,.8f} |" if price and price < 1 else f"| Price | ${price:,.2f} |",
                        f"| 24h Change | {change:+.1f}%" if change is not None else "| 24h Change | ? |",
                        f"| Liquidity | ${liq:,.0f} |" if liq else "| Liquidity | ? |",
                        f"| 24h Volume | ${vol:,.0f} |" if vol else "| 24h Volume | ? |",
                        f"| FDV | ${fdv:,.0f} |" if fdv else "",
                        f"| 24h Trades | {int(buys or 0)} buys / {int(sells or 0)} sells |" if buys is not None else "",
                        f"| DEX | {pair.get('dexId', '?')} |" if pair.get("dexId") else "",
                        f"| As of | {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} |",
                    ]
                    return {"_formatted": "\n".join(ln for ln in lines if ln)}
            except Exception as exc:
                logger.debug("coingecko formatted-table path failed: %s", exc)

            # DexScreener failed — try symbol-based fallbacks
            for fn in (_fetch_coincap_price, _fetch_cmc_price):
                result = await fn(symbol)
                if result and result.get("price"):
                    return result
            return None
        else:
            # EVM address — try CoinGecko by contract first
            try:
                from app.coingecko_connector import get_coingecko_connector

                data = await get_coingecko_connector().get_token_price_by_contract("ethereum", address)
                if data:
                    price = _to_float(data.get("usd"))
                    if price:
                        return {
                            "symbol": symbol.upper(),
                            "price": price,
                            "change_24h": _to_float(data.get("usd_24h_change")),
                            "market_cap": _to_float(data.get("usd_market_cap")),
                            "volume_24h": _to_float(data.get("usd_24h_vol")),
                            "source": "coingecko",
                        }
            except Exception as exc:
                logger.debug("coingecko quote path failed: %s", exc)

            # Fallback to symbol-based providers
            for fn in (_fetch_coincap_price, _fetch_cmc_price):
                result = await fn(symbol)
                if result and result.get("price"):
                    return result
            return None

    # --- Symbol-only path ---
    # 1. CoinGecko (rich data with extra market context)
    cg_id = _COINGECKO_IDS.get(symbol.lower())
    if cg_id:
        try:
            rich = await _fetch_rich_cg_data(symbol, cg_id)
            if rich and rich.get("price"):
                return rich
        except Exception as exc:
            logger.debug("rich coingecko fetch failed: %s", exc)

    # 2. CoinCap
    result = await _fetch_coincap_price(symbol)
    if result and result.get("price"):
        return result

    # 3. CoinMarketCap
    result = await _fetch_cmc_price(symbol)
    if result and result.get("price"):
        return result

    return None


def _format_rich_price(data: dict[str, Any]) -> str:
    """Format a rich price data dict into a structured markdown block."""
    symbol = data.get("symbol", "?")
    price = data.get("price")
    change = data.get("change_24h")
    mc = data.get("market_cap")
    vol = data.get("volume_24h")
    high = data.get("high_24h")
    low = data.get("low_24h")
    dom = data.get("dominance")
    source = data.get("source", "coingecko")

    if not price:
        return f"{symbol} price data unavailable"

    change_str = f" ({change:+.1f}%)" if change is not None else ""
    mc_str = f"${mc:,.0f}" if mc else "?"
    vol_str = f"${vol:,.0f}" if vol else "?"
    high_str = f"${high:,.2f}" if high else "?"
    low_str = f"${low:,.2f}" if low else "?"
    dom_str = f"{dom:.1f}%" if dom else "?"

    lines = [
        f"**{symbol} Price:** ${price:,.2f}{change_str}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Price | ${price:,.2f} |",
        f"| 24h Change | {change:+.1f}%" if change is not None else "| 24h Change | ? |",
        f"| Market Cap | {mc_str} |",
        f"| 24h Volume | {vol_str} |",
        f"| 24h High | {high_str} |",
        f"| 24h Low | {low_str} |",
    ]

    if dom and dom > 0:
        lines.append(f"| Market Dominance | {dom_str} |")

    lines.append(f"| Data Source | {source} |")
    lines.append(f"| As of | {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} |")

    return "\n".join(lines)


async def _exec_price(message: str) -> str | None:
    """Fetch a live token price with rich context. Returns structured data or None.

    Provider fallback chain:
      1. DexScreener (Solana address) / CoinGecko (EVM address / symbol)
      2. CoinCap (symbol)
      3. CoinMarketCap (symbol, if ``CMC_API_KEY`` set)
    """
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
        chain = _chain_for_address(address) if address else ""

        result = await _fetch_price_with_fallback(symbol, address=address, chain=chain)
        if not result:
            return None

        # If the Solana DexScreener path already produced a formatted string,
        # return it directly.
        formatted = result.pop("_formatted", None)
        if formatted:
            return formatted

        return _format_rich_price(result)
    except Exception as e:
        logger.debug("chat_tool_price_failed", error=str(e)[:120])
        return None
