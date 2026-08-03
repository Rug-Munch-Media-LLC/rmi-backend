"""V1 data endpoints — /api/v1/data/*.

Frontend-facing data surface (scan history). Wires the frontend
``getScanHistory()`` call at ``src/services/rmi/api.ts:907`` to recent
``scan_results`` persisted by the scanner pipeline.

Data sources (first hit wins):
  1. Postgres ``scan_results`` table (canonical — written by
     ``app.domains.scanner.unified.persistence.persist_result``)
  2. Redis cache keys ``scan_result:*`` / ``sentinelscan:*``
  3. Empty list (both backends unavailable — degraded, not broken)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/data", tags=["data"])


def _row_to_scan(row: Any) -> dict[str, Any]:
    """Map a scan_results ORM row to the frontend ScanRecord shape.

    Frontend contract (src/services/rmi/api.ts ScanRecord):
      id, contract_address, chain, token_name, token_symbol,
      risk_score, risk_level, is_honeypot, scanned_at
    News fetcher additionally reads is_rug_pull / liquidity_locked /
    has_mint_authority (defaulted conservatively).
    """
    risk_flags = [str(f).lower() for f in (row.risk_flags or [])]
    scanned_at = getattr(row, "scanned_at", None)
    return {
        "id": str(getattr(row, "id", "")),
        "contract_address": row.token_address,
        "token_address": row.token_address,
        "chain": row.chain,
        "token_name": row.name or row.symbol or "",
        "token_symbol": row.symbol or "",
        "risk_score": int(row.risk_score or 0),
        "risk_level": row.risk_level or "unknown",
        "is_honeypot": any("honeypot" in f for f in risk_flags),
        "is_rug_pull": any("rug" in f for f in risk_flags),
        "liquidity_locked": not any("unlocked" in f for f in risk_flags),
        "has_mint_authority": any("mint" in f or "owner" in f for f in risk_flags),
        "scanned_at": scanned_at.isoformat() if scanned_at else None,
    }


def _cache_to_scan(raw: str | bytes, key: str = "") -> dict[str, Any] | None:
    """Map a Redis-cached scan payload to the frontend ScanRecord shape."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    token_address = data.get("token_address") or data.get("contract_address") or ""
    if not token_address:
        return None
    scanned_at_raw = data.get("scanned_at") or ""
    scanned_at: str | None = None
    if scanned_at_raw:
        try:
            scanned_at = datetime.fromisoformat(str(scanned_at_raw).replace("Z", "+00:00")).astimezone(UTC).isoformat()
        except ValueError:
            scanned_at = str(scanned_at_raw)
    risk_flags = [str(f).lower() for f in (data.get("risk_flags") or [])]
    safety = int(data.get("safety_score") or 50)
    risk_score = int(data.get("risk_score") or max(0, 100 - safety))
    return {
        "id": data.get("id") or key,
        "contract_address": token_address,
        "token_address": token_address,
        "chain": data.get("chain", ""),
        "token_name": data.get("name") or data.get("token_name") or data.get("symbol") or "",
        "token_symbol": data.get("symbol") or "",
        "risk_score": risk_score,
        "risk_level": data.get("risk_level", "unknown"),
        "is_honeypot": bool(data.get("is_honeypot")) or any("honeypot" in f for f in risk_flags),
        "is_rug_pull": bool(data.get("is_rug_pull")) or any("rug" in f for f in risk_flags),
        "liquidity_locked": bool(data.get("liquidity_locked", True)),
        "has_mint_authority": bool(data.get("has_mint_authority"))
        or any("mint" in f or "owner" in f for f in risk_flags),
        "scanned_at": scanned_at,
    }


async def fetch_recent_scans(limit: int = 20, address: str | None = None) -> list[dict[str, Any]]:
    """Return recent scan results, newest first.

    Prefers Postgres ``scan_results``; falls back to Redis caches; returns
    an empty list when both are unavailable. ``address`` optionally filters
    to a single token address (case-insensitive).
    """
    rows = await _fetch_from_db(limit=limit, address=address)
    if rows:
        return rows
    return await _fetch_from_redis(limit=limit, address=address)


async def _fetch_from_db(limit: int, address: str | None) -> list[dict[str, Any]]:
    """Query the scan_results table via the app async session factory."""
    try:
        from sqlalchemy import select

        from app.core.database import async_session_factory
        from app.models.scan import ScanResult

        stmt = select(ScanResult).order_by(ScanResult.scanned_at.desc()).limit(limit)
        if address:
            stmt = stmt.where(ScanResult.token_address.ilike(address.strip()))
        async with async_session_factory() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        if rows:
            logger.info("scan_history_db_hit", count=len(rows), limit=limit)
        return [_row_to_scan(r) for r in rows]
    except Exception as exc:
        logger.warning("scan_history_db_miss err=%s", exc)
        return []


async def _fetch_from_redis(limit: int, address: str | None) -> list[dict[str, Any]]:
    """Fallback: read scan caches from Redis (scan_result:* / sentinelscan:*)."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return []
        keys: list[str] = []
        async for key in r.scan_iter(match="scan_result:*", count=500):
            keys.append(str(key))
        async for key in r.scan_iter(match="sentinelscan:*", count=500):
            keys.append(str(key))
        if not keys:
            return []
        scans: list[dict[str, Any]] = []
        for key in keys:
            raw = await r.get(key)
            scan = _cache_to_scan(raw, key=key)
            if scan is None:
                continue
            if address and address.strip().lower() != scan["contract_address"].lower():
                continue
            scans.append(scan)
        scans.sort(key=lambda s: s.get("scanned_at") or "", reverse=True)
        logger.info("scan_history_redis_hit", count=len(scans), limit=limit)
        return scans[:limit]
    except Exception as exc:
        logger.warning("scan_history_redis_miss err=%s", exc)
        return []


@router.get("/scan-history")
async def scan_history(
    address: str | None = Query(default=None, description="Filter by token contract address"),
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Recent token scan results for the frontend dashboard.

    Returns ``{"scans": [...], "count": N}`` matching the frontend
    ``ScanRecord`` contract. Scans are newest-first.
    """
    scans = await fetch_recent_scans(limit=limit, address=address)
    return {"scans": scans, "count": len(scans)}
