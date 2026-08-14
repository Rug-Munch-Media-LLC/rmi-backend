"""price_lookup tool — REAL token price/market data.

Sources (in order):
  1. Our backend ``/api/v1/databus/token/{address}`` — DataBus aggregate
     (DexScreener + Birdeye + GMGN + our scanners), cached by the backend.
  2. DexScreener search API (``/search/?q=<symbol>``) — direct, free.
  3. DexScreener token endpoint (``/tokens/{address}``) — direct, free.

Every number in the returned payload comes from one of those live sources.
If all sources fail the tool returns ``ok=False`` — the chat layer must say
"I don't have live data for that right now".
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import DEXSCREENER
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.price")


def _extract_pair(p: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a DexScreener pair into the price card contract."""
    base = p.get("baseToken") or {}
    addr = base.get("address", "")
    if not addr:
        return None
    return {
        "address": addr,
        "symbol": base.get("symbol", ""),
        "name": base.get("name", ""),
        "chain": p.get("chainId", ""),
        "dex": p.get("dexId", ""),
        "price_usd": p.get("priceUsd"),
        "price_native": p.get("priceNative"),
        "change_1h": (p.get("priceChange") or {}).get("h1"),
        "change_6h": (p.get("priceChange") or {}).get("h6"),
        "change_24h": (p.get("priceChange") or {}).get("h24"),
        "volume_24h": (p.get("volume") or {}).get("h24"),
        "liquidity_usd": p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else None,
        "market_cap": p.get("marketCap"),
        "fdv": p.get("fdv"),
        "pair_address": p.get("pairAddress"),
    }


def _parse_backend_detail(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the DataBus ``/token/{address}`` payload into pairs list."""
    pairs = body.get("pairs")
    if isinstance(pairs, list):
        return [_extract_pair(p) for p in pairs if isinstance(p, dict)]
    # Some payloads nest under ``data``.
    data = body.get("data")
    if isinstance(data, dict):
        pairs = data.get("pairs")
        if isinstance(pairs, list):
            return [_extract_pair(p) for p in pairs if isinstance(p, dict)]
    return []


async def price_lookup(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Resolve real price/market data for a token address or ``$SYMBOL``.

    Prefer the address path (exact). Symbol search returns the top matching
    pair from DexScreener.
    """
    if address:
        # 1. Our backend DataBus aggregate (own data first).
        body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=6.0)
        pairs = _parse_backend_detail(body) if body else []
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="backend:/api/v1/databus/token",
            )
        # 2. Direct DexScreener token endpoint (address is exact).
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = [_extract_pair(p) for p in (body or {}).get("pairs", []) if isinstance(p, dict)]
        pairs = [p for p in pairs if p is not None]
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="dexscreener:/tokens/{address}",
            )

    if symbol:
        q = symbol.strip().upper()
        body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
        pairs = [_extract_pair(p) for p in (body or {}).get("pairs", []) if isinstance(p, dict)]
        pairs = [p for p in pairs if p is not None]
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_symbol": symbol, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="dexscreener:/search",
            )

    return ToolResult(
        tool="price_lookup",
        ok=False,
        error="no live price data (backend + DexScreener unavailable or no match)",
    )
