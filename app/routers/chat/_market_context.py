"""Market context block for the chat system prompt.

Builds a compact, always-fresh "market context" block that is injected into
EVERY chat answer — both the website chat path and the Telegram bot path flow
through ``POST /api/v1/chat`` → ``_build_messages``, so injecting here covers
both.

Data sources (REUSED from the existing backend data layer — no reimplementation):
  - BTC/ETH price + 24h change: CoinGecko via ``market_intel.providers._cg``
    (the same cached client the market-intel routes use).
  - Fear & Greed index: alternative.me via ``market_intel.providers._fng``.
  - Top trending tokens: CoinGecko ``/search/trending`` via ``_cg`` — the
    backend equivalent of the bot's ``tools/trending.py``.

Freshness + prefix caching:
  - The built block is cached for 60s through the shared market-intel TTL
    cache (``_cget``/``_cset``) so chat messages never hit the upstream APIs
    more than once a minute.
  - The block contains NO minute-granularity timestamp. Only a day-granularity
    date is included (stable for the whole day → provider prefix caching is
    preserved). Prior finding: ``%Y-%m-%d %H:%M UTC`` in the system prompt
    broke prefix caching (see ``_messages.py``).

Zero fabrication (non-negotiable):
  - Every number is fetched live from the upstreams. If the core sources
    (prices or Fear & Greed) are missing, or the fetch exceeds the timeout,
    the whole block is omitted (returns "") and chat proceeds without it.
  - Trending is best-effort: when it fails, the block is still emitted with
    prices + Fear & Greed + verdict, but never with invented symbols.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.routers.market_intel.cache import _cget, _cset
from app.routers.market_intel.providers import _cg, _fng

logger = get_logger(__name__)

# ── Tuning ─────────────────────────────────────────────────────────────
MARKET_CONTEXT_CACHE_KEY = "chat:market_context_block"
MARKET_CONTEXT_TTL = 60  # seconds — same freshness budget as chat._CACHE_TTL_MARKET
MARKET_CONTEXT_FETCH_TIMEOUT = 3.0  # seconds — never let context delay chat
TRENDING_LIMIT = 5

_PRICE_PARAMS: dict[str, Any] = {
    "ids": "bitcoin,ethereum",
    "vs_currencies": "usd",
    "include_24hr_change": "true",
}


# ── Small formatters (real numbers only) ───────────────────────────────
def _fmt_price(value: float | int | None) -> str:
    """Whole-dollar price, or "" when the value is missing/non-positive."""
    if value is None:
        return ""
    v = float(value)
    if v <= 0:
        return ""
    return f"${v:,.0f}"


def _fmt_pct(value: float | int | None) -> str:
    """Signed 24h percent change, or "" when missing."""
    if value is None:
        return ""
    return f"{float(value):+.1f}%"


def market_verdict(
    fng_value: int | None,
    btc_change: float | int | None,
    eth_change: float | int | None,
) -> str:
    """One-line risk regime from Fear & Greed + BTC/ETH 24h momentum.

    Deterministic and computed only from real fetched numbers — never
    fabricated. Returns "" when the Fear & Greed input is missing (no basis
    for a verdict); otherwise ``risk-on`` / ``risk-off`` / ``neutral``.
    """
    if fng_value is None:
        return ""
    if fng_value >= 55 and (btc_change or 0) >= 0.5 and (eth_change or 0) >= 0.5:
        return "risk-on"
    if fng_value <= 40 or (btc_change or 0) <= -2.0 or (eth_change or 0) <= -2.0:
        return "risk-off"
    return "neutral"


# ── Fetchers (all fail-open to empty results) ──────────────────────────
async def _fetch_price_snapshot() -> dict[str, Any]:
    """CoinGecko BTC/ETH price + 24h change (upstream-cached by ``_cg``)."""
    data = await _cg("/simple/price", params=dict(_PRICE_PARAMS), ttl=60)
    if not isinstance(data, dict):
        return {}
    btc = data.get("bitcoin") or {}
    eth = data.get("ethereum") or {}
    return {
        "btc_price": btc.get("usd"),
        "btc_change": btc.get("usd_24h_change"),
        "eth_price": eth.get("usd"),
        "eth_change": eth.get("usd_24h_change"),
    }


async def _fetch_fear_greed() -> tuple[int | None, str | None]:
    """(value, classification) from alternative.me — (None, None) on failure."""
    fng_data = await _fng(ttl=300)
    if not isinstance(fng_data, dict) or not isinstance(fng_data.get("data"), list) or not fng_data["data"]:
        return None, None
    row = fng_data["data"][0]
    try:
        return int(row.get("value")), str(row.get("value_classification") or "")
    except (TypeError, ValueError):
        return None, None


async def _fetch_trending(limit: int = TRENDING_LIMIT) -> list[str]:
    """Up to ``limit`` REAL trending token symbols from CoinGecko (best-effort)."""
    trending = await _cg("/search/trending", ttl=120)
    if not isinstance(trending, dict) or not isinstance(trending.get("coins"), list):
        return []
    symbols: list[str] = []
    for coin in trending["coins"][:limit]:
        item = coin.get("item") or {}
        sym = item.get("symbol")
        if sym:
            symbols.append(str(sym).upper())
    return symbols


async def _fetch_all() -> tuple[dict[str, Any], tuple[int | None, str | None], list[str]]:
    """Fetch price snapshot + Fear & Greed + trending in parallel."""
    prices, fng, trending = await asyncio.gather(
        _fetch_price_snapshot(),
        _fetch_fear_greed(),
        _fetch_trending(),
    )
    return prices, fng, trending


# ── Public builder ─────────────────────────────────────────────────────
async def build_market_context_block() -> str:
    """Build the cached market-context block, or "" when sources are down.

    The block is cached for ``MARKET_CONTEXT_TTL`` (60s) via the shared
    market-intel TTL cache, so a second chat message within the window does
    not touch the upstream APIs. Any failure (network, timeout, partial data)
    returns "" — chat never waits on or breaks because of market context,
    and never receives fabricated numbers.
    """
    cached = _cget(MARKET_CONTEXT_CACHE_KEY, MARKET_CONTEXT_TTL)
    if cached is not None:
        return str(cached)

    try:
        prices, fng, trending = await asyncio.wait_for(
            _fetch_all(),
            timeout=MARKET_CONTEXT_FETCH_TIMEOUT,
        )
    except Exception as e:  # fail-open — market context is optional
        logger.debug("market_context_fetch_failed err=%s", str(e)[:120])
        return ""

    btc_price = _fmt_price(prices.get("btc_price"))
    eth_price = _fmt_price(prices.get("eth_price"))
    if not btc_price or not eth_price:
        return ""  # core price source down — never fabricate prices

    fng_value, fng_class = fng
    if fng_value is None:
        return ""  # verdict needs Fear & Greed — omit rather than invent

    btc_change = _fmt_pct(prices.get("btc_change"))
    eth_change = _fmt_pct(prices.get("eth_change"))
    btc_part = f"BTC {btc_price}" + (f" ({btc_change} 24h)" if btc_change else "")
    eth_part = f"ETH {eth_price}" + (f" ({eth_change} 24h)" if eth_change else "")
    verdict = market_verdict(fng_value, prices.get("btc_change"), prices.get("eth_change"))

    lines = [
        f"MARKET CONTEXT (live, as of {datetime.now(UTC).strftime('%Y-%m-%d')}):",
        f"{btc_part} | {eth_part}",
        f"Fear & Greed: {fng_value} ({fng_class or 'Neutral'}) | Verdict: {verdict or 'neutral'}",
    ]
    if trending:
        lines.append(f"Trending: {', '.join(trending)}")

    block = "\n".join(lines)
    _cset(MARKET_CONTEXT_CACHE_KEY, block)
    return block
