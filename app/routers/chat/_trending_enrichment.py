"""Trending-token enrichment with OUR OWN indexer + scanner data.

For each trending token surfaced in the chat market-context block
(``_market_context.build_market_context_block`` — CoinGecko trending
symbols), resolve the symbol to OUR indexed token (``token_meta_ref`` in
ClickHouse) and enrich it with our own dataset, not just external APIs:

  - 24h DEX volume                    (dex_events → ``own_dex_data``)
  - holder count + top-holder pct     (evm_transfers → ``own_holder_analysis``)
  - deployer age + prior tokens       (deployer_events → ``own_deployer_timeline``)
  - bundle / sniper flag              (evm_transfers → ``own_bundle_detect``)

Fail-open by design: any ClickHouse / auth / empty-data failure degrades
silently to external-API-only — the market context never gains "unverified"
noise, never delays the answer (bounded concurrent enrichment with
timeouts, capped at the top ~5 movers), and never fabricates numbers.

Wiring: ``_market_context`` starts ``enrich_trending_lines`` as a task
concurrently with its core price/F&G/trending fetch, then awaits it with a
short timeout. See ``_market_context.build_market_context_block``.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from app.core.logging import get_logger

logger = get_logger("chat.trending_enrichment")

# ── Tuning ────────────────────────────────────────────────────────────────
MAX_TOKENS = 5  # cap: only the top movers get enriched
PER_TOKEN_TIMEOUT = 2.0  # seconds — one token's engines must finish in this window

_CHAIN_NORM = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    """Normalize a chain name to the ClickHouse ``wallet_memory`` format."""
    return _CHAIN_NORM.get((chain or "").lower(), (chain or "").lower())


# ── Lazy per-thread ClickHouse client ─────────────────────────────────────
# clickhouse_driver.Client rejects concurrent queries on one connection, and
# per-token enrichment tasks run concurrently (asyncio.to_thread workers).
# One client per worker thread keeps each connection sequential-safe while
# reusing connections. Mirrors the scanner engines' pattern.
_ch_local = threading.local()


def _get_ch_client() -> Any | None:
    """Lazy ClickHouse client for the ``wallet_memory`` database."""
    cached = getattr(_ch_local, "client", None)
    if cached is not None:
        return cached

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient
        from clickhouse_driver.errors import Error as CHDriverError
    except ImportError:
        logger.debug("clickhouse_driver not installed; indexer enrichment unavailable")
        return None

    try:
        candidate: Any = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=10,
        )
        candidate.execute("SELECT 1")
    except (ConnectionError, TimeoutError, RuntimeError, OSError, AttributeError, CHDriverError) as exc:
        logger.debug("indexer enrichment ClickHouse unavailable: %s", str(exc).splitlines()[0][:200])
        return None
    _ch_local.client = candidate
    logger.debug("indexer enrichment ClickHouse connected (thread client)")
    return _ch_local.client


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def enrich_trending_lines(limit: int = MAX_TOKENS) -> list[str]:
    """Render one compact enrichment line per enriched trending token.

    Pulls the same cached CoinGecko trending the market context uses, then
    resolves each symbol against OUR ``token_meta_ref`` index and enriches
    the top ``limit`` with our own scanner engines. Fail-open: returns []
    when any source is down and never raises.
    """
    try:
        from app.routers.market_intel.providers import _cg

        data = await _cg("/search/trending", ttl=120)
    except Exception as exc:
        logger.debug("trending_enrichment_symbols_failed err=%s", str(exc)[:120])
        return []
    if not isinstance(data, dict) or not isinstance(data.get("coins"), list):
        return []

    symbols: list[str] = []
    for coin in data["coins"][:limit]:
        item = coin.get("item") or {}
        sym = (item.get("symbol") or "").upper()
        if sym:
            symbols.append(sym)

    # Resolve each surfaced symbol to OUR indexed token (best-effort).
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for sym in symbols:
        token = await _resolve_indexed_token(sym)
        if token is None:
            continue
        key = f"{token['chain']}:{token['token_address']}"
        if key in seen:
            continue
        seen.add(key)
        token["symbol"] = sym
        resolved.append(token)
    if not resolved:
        return []

    # Concurrent, bounded per-token enrichment.
    lines: list[str] = []
    results = await asyncio.gather(
        *[_enrich_token_bounded(t) for t in resolved],
        return_exceptions=True,
    )
    for token, result in zip(resolved, results, strict=True):
        if isinstance(result, BaseException) or not isinstance(result, dict):
            continue
        line = _render_line(token.get("symbol", ""), token.get("token_address", ""), result)
        if line:
            lines.append(line)
    return lines


# ═══════════════════════════════════════════════════════════════════════════
# Per-token enrichment
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_indexed_token(symbol: str) -> dict[str, str] | None:
    """Resolve a trending symbol to OUR indexed (token_address, chain).

    Looks up ``wallet_memory.token_meta_ref`` — the indexer's own
    symbol→token reference table. Returns None when ClickHouse is down or
    the symbol is not in our index (fail-open).
    """
    if not symbol:
        return None

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return client.execute(
                """
                SELECT token_address, chain
                FROM wallet_memory.token_meta_ref
                WHERE lower(symbol) = %(sym)s
                  AND symbol != ''
                ORDER BY last_updated DESC
                LIMIT 1
                """,
                {"sym": symbol.lower()},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_meta_ref resolve failed: %s", exc)
            return []

    rows = await asyncio.to_thread(_query)
    if not rows or not rows[0]:
        return None
    addr = str(rows[0][0] or "").lower()
    chain = _normalize_chain(str(rows[0][1] or ""))
    if not addr or not chain:
        return None
    return {"token_address": addr, "chain": chain}


async def _lookup_deployer(token_address: str, chain: str) -> str | None:
    """Best-effort deployer EOA for a token from our ``deployer_events``."""
    if not token_address or not chain:
        return None

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return client.execute(
                """
                SELECT argMin(if(deployer_eoa != '', deployer_eoa, deployer), block_timestamp)
                FROM wallet_memory.deployer_events
                WHERE chain = %(chain)s
                  AND lower(token_address) = %(token)s
                LIMIT 1
                """,
                {"chain": chain, "token": token_address.lower()},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("deployer lookup failed: %s", exc)
            return []

    rows = await asyncio.to_thread(_query)
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0]).lower()
    return None


async def _enrich_token_bounded(token: dict[str, str]) -> dict[str, Any]:
    """Enrich one token with our own engines, bounded by PER_TOKEN_TIMEOUT."""
    try:
        return await asyncio.wait_for(_enrich_token(token), timeout=PER_TOKEN_TIMEOUT)
    except (TimeoutError, Exception) as exc:
        logger.debug("trending_enrich_token_failed %s err=%s", token.get("token_address", "")[:16], str(exc)[:120])
        return {}


async def _enrich_token(token: dict[str, str]) -> dict[str, Any]:
    """Run the four own-data engines concurrently for one token.

    Every engine is fail-open (returns ``result: unavailable`` dicts instead
    of raising), so a missing/empty ClickHouse row simply yields no signals.
    """
    address = token["token_address"]
    chain = token["chain"]

    async def _volume() -> dict[str, Any]:
        try:
            from app.domains.scanner.engines.own_dex_data.query import get_own_dex_data

            dex = await get_own_dex_data(address, chain, hours_24=24)
            if not isinstance(dex, dict) or dex.get("source") != "own":
                return {}
            metrics = dex.get("metrics") or {}
            vol = float(metrics.get("total_volume_24h") or 0)
            return {"volume_24h": vol} if vol > 0 else {}
        except Exception:
            return {}

    async def _holders() -> dict[str, Any]:
        try:
            from app.domains.scanner.engines.own_holder_analysis import get_own_holder_analysis

            h = await get_own_holder_analysis(address, chain)
            if not isinstance(h, dict) or h.get("result") != "scanned":
                return {}
            count = int(h.get("holder_count") or 0)
            top5 = float(h.get("top5_pct") or 0)
            out: dict[str, Any] = {}
            if count > 0:
                out["holder_count"] = count
            if top5 > 0:
                out["top5_pct"] = top5
            return out
        except Exception:
            return {}

    async def _deployer() -> dict[str, Any]:
        try:
            deployer = await _lookup_deployer(address, chain)
            if not deployer:
                return {}
            from app.domains.scanner.engines.own_deployer_timeline import get_own_deployer_timeline

            tl = await get_own_deployer_timeline(deployer, chain)
            if not isinstance(tl, dict) or tl.get("result") != "scanned":
                return {}
            out: dict[str, Any] = {}
            age = tl.get("deployer_age_days")
            if isinstance(age, (int, float)) and age > 0:
                out["deployer_age_days"] = age
            prior = tl.get("prior_tokens")
            if isinstance(prior, (int, float)) and prior > 0:
                out["prior_tokens"] = prior
            return out
        except Exception:
            return {}

    async def _bundle() -> dict[str, Any]:
        try:
            from app.domains.scanner.engines.own_bundle_detect import get_own_bundle_analysis

            b = await get_own_bundle_analysis(address, chain)
            if not isinstance(b, dict):
                return {}
            return {"is_bundled": bool(b.get("is_bundled"))}
        except Exception:
            return {}

    volume, holders, deployer, bundle = await asyncio.gather(
        _volume(),
        _holders(),
        _deployer(),
        _bundle(),
    )
    result: dict[str, Any] = {}
    result.update(volume)
    result.update(holders)
    result.update(deployer)
    result.update(bundle)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Rendering (real numbers only — nothing fabricated)
# ═══════════════════════════════════════════════════════════════════════════


def _fmt_count(value: float | int) -> str:
    """Compact human count: 1234 → '1.2k', 3_200_000 → '3.2M'."""
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}m"
    if v >= 1_000:
        return f"{v / 1_000:.1f}k"
    return f"{int(v)}"


def _fmt_age_days(value: float | int) -> str:
    """Deployer age in days: 0.4 → '<1d', 14.2 → '14d'."""
    v = float(value)
    if v < 1:
        return "<1d"
    return f"{int(v)}d"


def _short_addr(address: str) -> str:
    """0x1234567890abcdef... → 0x1234…cdef (compact disambiguation)."""
    a = str(address)
    if len(a) <= 12:
        return a
    return f"{a[:6]}…{a[-4:]}"


def _render_line(symbol: str, address: str, data: dict[str, Any]) -> str:
    """Render 'SYM (our data: N holders · top5 P% · deployer 14d · bundle detected)'.

    Returns "" when the token has no own-data signals — the token is then
    simply dropped (external-only, no 'unverified' noise).
    """
    parts: list[str] = []

    holder_count = data.get("holder_count")
    if isinstance(holder_count, (int, float)) and holder_count > 0:
        parts.append(f"{_fmt_count(holder_count)} holders")

    top5 = data.get("top5_pct")
    if isinstance(top5, (int, float)) and top5 > 0:
        parts.append(f"top5 {top5:.0f}%")

    age = data.get("deployer_age_days")
    if isinstance(age, (int, float)) and age > 0:
        parts.append(f"deployer {_fmt_age_days(age)}")

    prior = data.get("prior_tokens")
    if isinstance(prior, (int, float)) and prior > 0:
        parts.append(f"{int(prior)} prior tokens")

    if data.get("is_bundled"):
        parts.append("bundle detected")

    volume = data.get("volume_24h")
    if isinstance(volume, (int, float)) and volume > 0:
        parts.append(f"dex vol {_fmt_count(volume)}")

    if not parts:
        return ""

    label = f"{symbol} ({_short_addr(address)})" if address else symbol
    return f"{label}: our data: {' · '.join(parts)}"
