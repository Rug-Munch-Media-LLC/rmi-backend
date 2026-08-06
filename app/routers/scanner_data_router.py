"""Scanner Data Cache Router — cached scan results for RugCharts, RugMaps, Portfolio.

Reads from Redis cache key ``sentinelscan:{chain}:{address}`` populated by
the unified scanner v2 (``persist_result`` in ``persistence.py``). No re-scan
needed — all endpoints return cached data in <5ms.

Fallback: when Redis is unavailable or the cache key is missing, returns a
``cached: false`` response so frontend can fall through to DataBus / DexScreener.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.core.redis import get_redis_async

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v2/scanner/token", tags=["scanner-data"])

CACHE_TTL_SECONDS = 3600  # same as persist_result — 1 hour


async def _read_cache(chain: str, address: str) -> dict[str, Any] | None:
    """Read a scanner result from Redis cache."""
    try:
        r = get_redis_async()
        if r is None:
            return None
        cache_key = f"sentinelscan:{chain}:{address}"
        raw = await r.get(cache_key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("scanner_cache_read_failed", chain=chain, address=address, error=str(exc)[:120])
        return None


def _make_cached_response(data: dict[str, Any] | None) -> dict[str, Any]:
    """Wrap cached data (or None) in a uniform response envelope."""
    if data is None:
        return {"cached": False, "data": None}
    return {"cached": True, "data": data}


# ── Price ────────────────────────────────────────────────────────────────


@router.get("/price/{chain}/{address}", summary="Get cached scanner price data")
async def get_scanner_price(chain: str, address: str) -> dict[str, Any]:
    """Return cached price data from the last scanner run.

    Fields returned: price_usd, market_cap_usd, liquidity_usd, volume_24h_usd,
    name, symbol, scanned_at, age_hours.
    """
    cached = await _read_cache(chain, address)
    if cached is None:
        return _make_cached_response(None)

    price_data = {
        "price_usd": cached.get("price_usd", 0),
        "market_cap_usd": cached.get("market_cap_usd", 0),
        "liquidity_usd": cached.get("liquidity_usd", 0),
        "volume_24h_usd": cached.get("volume_24h_usd", 0),
        "name": cached.get("name", ""),
        "symbol": cached.get("symbol", ""),
        "age_hours": cached.get("age_hours", 0),
        "scanned_at": cached.get("scanned_at", ""),
        "chain": cached.get("chain", chain),
        "token_address": cached.get("token_address", address),
    }
    return _make_cached_response(price_data)


# ── Holders ──────────────────────────────────────────────────────────────


@router.get("/holders/{chain}/{address}", summary="Get cached holder distribution")
async def get_scanner_holders(chain: str, address: str) -> dict[str, Any]:
    """Return cached holder distribution from the last scanner run.

    Includes wallet freshness breakdown (fresh/established), exchange funding
    analysis, and scam-overlap data when available.
    """
    cached = await _read_cache(chain, address)
    if cached is None:
        return _make_cached_response(None)

    holder_info: dict[str, Any] = {
        "token_address": cached.get("token_address", address),
        "chain": cached.get("chain", chain),
        "scanned_at": cached.get("scanned_at", ""),
    }

    # SENTINEL holder_analysis (Phase 5 — most detailed)
    sentinel = cached.get("sentinel")
    if isinstance(sentinel, dict):
        free_mods = sentinel.get("free", {})
        holder_analysis = free_mods.get("holder_analysis") if isinstance(free_mods, dict) else None
        if isinstance(holder_analysis, dict):
            holder_info["holder_analysis"] = holder_analysis
            holder_info["source"] = "sentinel_holder_analysis"

    # Wallet freshness (Phase 2b — own-data engine)
    wallet_freshness = cached.get("wallet_freshness")
    if isinstance(wallet_freshness, dict):
        holder_info["wallet_freshness"] = wallet_freshness

    # Funding analysis (Phase 2b)
    funding = cached.get("funding_analysis")
    if isinstance(funding, dict):
        holder_info["funding_analysis"] = funding

    # Holder overlap vs known scam tokens (Phase 2b)
    overlap = cached.get("holder_overlap")
    if isinstance(overlap, dict):
        holder_info["holder_overlap"] = overlap

    return _make_cached_response(holder_info)


# ── Liquidity ────────────────────────────────────────────────────────────


@router.get("/liquidity/{chain}/{address}", summary="Get cached LP analysis")
async def get_scanner_liquidity(chain: str, address: str) -> dict[str, Any]:
    """Return cached LP analysis from the last scanner run.

    Includes liquidity pool health, lock status, concentration metrics (HHI),
    and SENTINEL liquidity verification results.
    """
    cached = await _read_cache(chain, address)
    if cached is None:
        return _make_cached_response(None)

    lp_info: dict[str, Any] = {
        "token_address": cached.get("token_address", address),
        "chain": cached.get("chain", chain),
        "price_usd": cached.get("price_usd", 0),
        "liquidity_usd": cached.get("liquidity_usd", 0),
        "market_cap_usd": cached.get("market_cap_usd", 0),
        "volume_24h_usd": cached.get("volume_24h_usd", 0),
        "scanned_at": cached.get("scanned_at", ""),
    }

    # SENTINEL liquidity_verification (Phase 5)
    sentinel = cached.get("sentinel")
    if isinstance(sentinel, dict):
        free_mods = sentinel.get("free", {})
        liq_verification = free_mods.get("liquidity_verification") if isinstance(free_mods, dict) else None
        if isinstance(liq_verification, dict):
            lp_info["liquidity_verification"] = liq_verification
            lp_info["source"] = "sentinel_liquidity_verification"

    # Liquidity risk from deep phase
    liquidity_risk = cached.get("liquidity")
    if isinstance(liquidity_risk, dict):
        lp_info["liquidity_risk"] = liquidity_risk

    # Holder health (HHI, concentration)
    holder_health = cached.get("holder_health")
    if isinstance(holder_health, dict) and holder_health.get("hhi") is not None:
        lp_info["holder_health"] = holder_health

    return _make_cached_response(lp_info)


# ── Risk ─────────────────────────────────────────────────────────────────


@router.get("/risk/{chain}/{address}", summary="Get cached risk scores")
async def get_scanner_risk(chain: str, address: str) -> dict[str, Any]:
    """Return cached risk scores from the last scanner run.

    Includes safety_score, risk_level, all risk_flags, green_flags,
    confidence, breakdown, evidence, and any SENTINEL deep-forensic findings.
    """
    cached = await _read_cache(chain, address)
    if cached is None:
        return _make_cached_response(None)

    risk_data: dict[str, Any] = {
        "token_address": cached.get("token_address", address),
        "chain": cached.get("chain", chain),
        "name": cached.get("name", ""),
        "symbol": cached.get("symbol", ""),
        "safety_score": cached.get("safety_score", 50),
        "risk_score": round(100 - float(cached.get("safety_score", 50)), 2),
        "risk_level": cached.get("risk_level", "unknown"),
        "confidence": cached.get("confidence", 0),
        "risk_flags": cached.get("risk_flags", []),
        "green_flags": cached.get("green_flags", []),
        "scanned_at": cached.get("scanned_at", ""),
        "modules_run": cached.get("modules_run", []),
        "breakdown": cached.get("breakdown"),
        "evidence": cached.get("evidence"),
        "tier": cached.get("tier", "free"),
        "scan_duration_ms": cached.get("scan_duration_ms", 0),
    }

    # SENTINEL premium risk fields (if deep scan was run)
    sentinel = cached.get("sentinel")
    if isinstance(sentinel, dict):
        risk_data["sentinel"] = {
            "premium_unlocked": sentinel.get("premium_unlocked", False),
            "premium_preview": sentinel.get("premium_preview", []),
        }

    # Event correlation (news → price events)
    if cached.get("event_correlation"):
        risk_data["event_correlation"] = cached["event_correlation"]

    return _make_cached_response(risk_data)


# ── Full (all data at once) ─────────────────────────────────────────────


@router.get("/full/{chain}/{address}", summary="Get all cached scanner data")
async def get_scanner_full(chain: str, address: str) -> dict[str, Any]:
    """Return the complete cached scan result.

    Convenience endpoint that returns everything the scanner cached.
    Use the individual endpoints above for targeted queries.
    """
    cached = await _read_cache(chain, address)
    if cached is None:
        return _make_cached_response(None)

    return _make_cached_response(cached)
