"""Price executor for the chat tool-use layer.

Best-effort async executor that calls the DIRECT data modules (CoinGecko
connector / DexScreener) — never the x402 endpoints. Returns rich price
context with market data. Fail-open: returns ``None`` on any error/timeout.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    "uni": "uniswap",
    "aave": "aave",
    "link": "chainlink",
    "ldo": "lido-dao",
    "ondo": "ondo-finance",
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
    except Exception:
        pass

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
    lines.append(f"| As of | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} |")

    return "\n".join(lines)


async def _exec_price(message: str) -> str | None:
    """Fetch a live token price with rich context. Returns structured data or None."""
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
                liq = (
                    _to_float(pair.get("liquidity", {}).get("usd")) if isinstance(pair.get("liquidity"), dict) else None
                )
                vol = _to_float(pair.get("volume", {}).get("h24")) if isinstance(pair.get("volume"), dict) else None
                fdv = _to_float(pair.get("fdv"))
                txns_24h = pair.get("txns", {}).get("h24", {})
                buys = _to_float(txns_24h.get("buys"))
                sells = _to_float(txns_24h.get("sells"))
                pair_label = str(pair.get("baseToken", {}).get("symbol", symbol.upper()))

                lines = [
                    f"**${pair_label}** `${address[:18]}…` on **Solana**",
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
                    f"| As of | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} |",
                ]
                return "\n".join(l for l in lines if l)
            else:
                from app.coingecko_connector import get_coingecko_connector

                data = await get_coingecko_connector().get_token_price_by_contract("ethereum", address)
                if not data:
                    return None
                price = _to_float(data.get("usd"))
                change = _to_float(data.get("usd_24h_change"))
                mc = _to_float(data.get("usd_market_cap"))
                vol = _to_float(data.get("usd_24h_vol"))
                return _format_rich_price(
                    {
                        "symbol": symbol.upper(),
                        "price": price,
                        "change_24h": change,
                        "market_cap": mc,
                        "volume_24h": vol,
                        "source": "coingecko",
                    }
                )
        else:
            cg_id = _COINGECKO_IDS.get(symbol.lower())
            if not cg_id:
                return None
            rich = await _fetch_rich_cg_data(symbol, cg_id)
            if not rich or not rich.get("price"):
                return None
            return _format_rich_price(rich)
    except Exception as e:
        logger.debug("chat_tool_price_failed", error=str(e)[:120])
        return None
