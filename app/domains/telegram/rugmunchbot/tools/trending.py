"""trending tool — REAL currently-trending tokens.

Sources (in order):
  1. Our backend ``/api/v1/market/trending`` — fused RugCharts + DexScreener
     + CoinGecko trending (own-data first, backend-cached).
  2. Backend ``/api/v1/news/trending`` — trending news headlines.
  3. DexScreener token-boosts (``/token-boosts/top/v1``) — direct, free.

All token symbols/addresses in the result come from those live sources.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import DEXSCREENER_TOKEN_BOOSTS
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.trending")

# Chain-name aliases -> canonical DexScreener-style chain id. Mirrors
# tools/intent.py + tools/trend_algo.py (keep in sync). Used to filter the
# cross-chain feed down to the chain the user actually asked about.
_CHAIN_ALIASES: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "binance-smart-chain": "bsc",
    "binance": "bsc",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "zksync": "zksync",
    "robinhood": "robinhood",
    "injective": "injective",
    "fantom": "fantom",
    "ftm": "fantom",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "scroll": "scroll",
    "mantle": "mantle",
    "tron": "tron",
    "trx": "tron",
    "ton": "ton",
    "base": "base",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "mode": "mode",
}


def _normalize_chain(chain: str) -> str:
    """Return canonical chain id ('' / 'all' / 'multi' stay as-is)."""
    key = (chain or "").strip().lower()
    if key in ("", "all", "any", "multi"):
        return key
    return _CHAIN_ALIASES.get(key, key)


def _filter_tokens_by_chain(tokens: list[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Keep only tokens on the requested chain (pure, deterministic).

    ``chain`` '' / 'all' / 'multi' -> passthrough (cross-chain feed).
    Otherwise a token is kept when its normalized ``chain`` field equals the
    requested chain. CoinGecko entries carry ``chain == "multi"`` and no
    on-chain address, so they are dropped for a chain-scoped question (they
    are not scanable and not on the requested chain).
    """
    want = _normalize_chain(chain)
    if want in ("", "all", "multi"):
        return list(tokens)
    return [t for t in tokens if _normalize_chain(str(t.get("chain") or "")) == want]


async def _fetch_market_trending(chain: str = "all", limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await backend_json_get("/api/v1/market/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return None
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return None
    tokens: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": it.get("symbol") or "",
                "name": it.get("name") or "",
                "address": it.get("address") or it.get("token_address") or "",
                "chain": it.get("chain") or "",
                "price_usd": it.get("price"),
                "change_24h": it.get("change_24h"),
                "volume_24h": it.get("volume_24h"),
                "market_cap": it.get("market_cap"),
                "rank": it.get("rank"),
                "source": it.get("source") or "backend",
            }
        )
    tokens = _filter_tokens_by_chain(tokens, chain)
    if not tokens:
        return None
    return tokens, "backend:/api/v1/market/trending"


async def _fetch_token_boosts(chain: str = "all", limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await http_get_json(DEXSCREENER_TOKEN_BOOSTS, timeout=6.0)
    if not isinstance(body, list) or not body:
        return None
    tokens: list[dict[str, Any]] = []
    for it in body[:limit]:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "name": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "address": it.get("tokenAddress") or "",
                "chain": it.get("chainId") or "",
                "url": it.get("url") or "",
                "source": "dexscreener:token-boosts",
            }
        )
    tokens = _filter_tokens_by_chain(tokens, chain)
    if not tokens:
        return None
    return tokens, "dexscreener:token-boosts"


async def trending(*, chain: str = "all", limit: int = 10) -> ToolResult:
    """Fetch real trending tokens, chain-filtered (backend fused source first).

    Args:
        chain: canonical chain id ("solana", "ethereum", "base", ...) or
            "all" for the raw cross-chain feed. The user's requested chain
            must arrive here via the intent router (tool_kwargs) — this tool
            FILTERS the live sources, it never fabricates a chain.
        limit: max tokens to return.
    """
    tokens, source = (
        (await _fetch_market_trending(chain, limit)) or (await _fetch_token_boosts(chain, limit)) or (None, "")
    )
    if tokens is None:
        return ToolResult(tool="trending", ok=False, error="no live trending data (backend + DexScreener unavailable)")

    return ToolResult(
        tool="trending",
        ok=True,
        data={"chain": _normalize_chain(chain), "limit": limit, "tokens": tokens},
        symbols=[t["symbol"] for t in tokens if t.get("symbol")],
        addresses=[t["address"] for t in tokens if t.get("address")],
        source=source,
    )


async def news_headlines(*, limit: int = 5) -> ToolResult:
    """Real top news headlines from our backend news feed."""
    body = await backend_json_get("/api/v1/news/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return ToolResult(
            tool="news_headlines", ok=False, error="no live news (backend /api/v1/news/trending unavailable)"
        )
    items = body.get("items") or body.get("articles") or []
    headlines = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if title:
            headlines.append({"title": title, "url": it.get("url") or "", "source": it.get("source") or ""})
    if not headlines:
        return ToolResult(tool="news_headlines", ok=False, error="no headlines returned")
    return ToolResult(
        tool="news_headlines",
        ok=True,
        data={"headlines": headlines},
        symbols=[],
        addresses=[],
        source="backend:/api/v1/news/trending",
    )
