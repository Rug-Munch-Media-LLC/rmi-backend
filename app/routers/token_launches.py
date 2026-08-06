"""
Token Launch Monitoring Router — Real-time new token discovery API.
==================================================================
Endpoints:
  GET /api/v1/launches/feed      — Recent token launches (with risk scores)
  GET /api/v1/launches/alerts    — High-risk launch alerts
  GET /api/v1/launches/scan/{address} — Get auto-scan result for a launch
  GET /api/v1/launches/stats     — Launch activity stats
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/launches", tags=["launches"])


@router.get("/feed", summary="Recent token launches")
async def get_launch_feed(
    chain: str = Query(default="solana", description="Blockchain filter"),
    limit: int = Query(default=50, ge=1, le=200),
    min_liquidity: float = Query(default=0, ge=0, description="Min liquidity filter"),
) -> dict[str, Any]:
    """
    Get recently detected token launches with risk scores.

    Returns list of new token launches discovered by the detection system.
    Includes risk scores from auto-scan, liquidity, age, and source.
    """
    try:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()
        launches = await detector.get_recent_launches(limit=min(limit, 200))

        # Filter by chain
        if chain and chain != "all":
            launches = [l for l in launches if l.get("chain") == chain]

        # Filter by liquidity
        if min_liquidity > 0:
            launches = [l for l in launches if float(l.get("liquidity", 0)) >= min_liquidity]

        # Enrich with scan results
        enriched = []
        for launch in launches:
            address = launch.get("address", "")
            if address:
                try:
                    from app.core.redis import get_redis_async

                    r = get_redis_async()
                    if r:
                        scan_raw = await r.get(f"launch_scan:{address}")
                        if scan_raw:
                            scan = json.loads(scan_raw)
                            launch["risk_score"] = scan.get("risk_score", 50)
                            launch["risk_flags"] = scan.get("risk_flags", [])
                            launch["scanned_at"] = scan.get("scanned_at")
                except Exception:
                    pass
            enriched.append(launch)

        return {
            "launches": enriched,
            "count": len(enriched),
            "chain": chain,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.warning("launch_feed_failed: %s", e)
        return {"launches": [], "count": 0, "error": str(e)}


@router.get("/alerts", summary="High-risk launch alerts")
async def get_launch_alerts(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """
    Get recent high-risk launch alerts.

    Tokens that scored above 70/100 on the auto-scan risk assessment.
    Includes risk flags and metadata.
    """
    try:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()
        alerts = await detector.get_launch_alerts(limit)
        return {
            "alerts": alerts,
            "count": len(alerts),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e)}


@router.get("/scan/{address}", summary="Get auto-scan result for a launch")
async def get_launch_scan(address: str) -> dict[str, Any]:
    """
    Get the automated scan result for a recently launched token.

    Returns risk score, flags, liquidity data from the auto-scan pipeline.
    """
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r:
            raw = await r.get(f"launch_scan:{address}")
            if raw:
                return {"address": address, "scan": json.loads(raw), "found": True}
    except Exception:
        pass

    return {"address": address, "scan": None, "found": False}


@router.get("/stats", summary="Launch activity statistics")
async def get_launch_stats() -> dict[str, Any]:
    """
    Get statistics about recent token launch activity.

    Total launches detected, high-risk count, average liquidity, etc.
    """
    try:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()
        launches = await detector.get_recent_launches(200)
        alerts = await detector.get_launch_alerts(20)

        lps = [float(l.get("liquidity", 0)) for l in launches if l.get("liquidity")]
        high_risk = [l for l in launches if l.get("risk_score", 0) >= 70]

        return {
            "total_launches_24h": len(launches),
            "high_risk_launches": len(high_risk),
            "alerts_generated": len(alerts),
            "avg_liquidity_usd": round(sum(lps) / max(len(lps), 1), 2),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {"error": str(e)}
