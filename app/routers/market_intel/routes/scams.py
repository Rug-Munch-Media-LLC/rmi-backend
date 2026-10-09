"""
Market Intelligence - Scam Detection & Alerts Feed Routes
Scam alerts from DexScreener suspicious tokens and combined alerts feed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.logging import get_logger
from app.routers.market_intel.providers import _cg, _ds
from app.routers.market_intel.scams_helpers import (
    _build_alert_item,
    _build_scam_item,
    _classify_alert,
    _classify_scam,
    _extract_token_data,
    _fetch_pairs_for_boosts,
)
from app.routers.market_intel.utils import _now, _safe_float

logger = get_logger(__name__)
router = APIRouter(tags=["market-intel"])


@router.get("/scams")
async def get_market_scams(
    request: Request,
    limit: int = Query(5, ge=1, le=50),
    tier: str = Query("free"),
) -> dict[str, Any]:
    """Scam alerts - flagged tokens from DexScreener with suspicious patterns."""
    now = _now()
    items = []
    boosts = await _ds("/token-boosts/latest/v1", ttl=60)
    if boosts and isinstance(boosts, list):
        trending = await _cg("/search/trending", ttl=120)
        trending_symbols = set()
        if trending and "coins" in trending:
            for c in trending["coins"]:
                trending_symbols.add(c.get("item", {}).get("symbol", "").upper())

        boosts = boosts[:20]
        processed = await _fetch_pairs_for_boosts(boosts)
        for b, pairs_data in processed:
            addr = b.get("tokenAddress", "")
            chain = b.get("chainId", "solana")
            token_data = _extract_token_data(pairs_data, addr[:6].upper())

            scam_type, severity, indicators = _classify_scam(token_data)

            if not scam_type:
                continue

            items.append(_build_scam_item(b, chain, token_data, scam_type, severity, indicators, now))

    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}


@router.get("/alerts-feed")
async def get_market_alerts_feed(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    tier: str = Query("free"),
) -> dict[str, Any]:
    """Combined alerts feed - scams from DexScreener, system alerts, bad actors."""
    now = _now()
    items = []

    # 1. Scam alerts from DexScreener suspicious tokens
    boosts = await _ds("/token-boosts/latest/v1", ttl=60)
    if boosts and isinstance(boosts, list):
        boosts = boosts[:30]
        processed = await _fetch_pairs_for_boosts(boosts)
        for b, pairs_data in processed:
            addr = b.get("tokenAddress", "")
            chain = b.get("chainId", "solana")
            token_data = _extract_token_data(pairs_data, addr[:6].upper())
            alert_type, severity, desc = _classify_alert(token_data)
            items.append(_build_alert_item(b, chain, token_data, alert_type, severity, desc, now))

    # 2. System alerts from the existing alerts endpoint (internal fetch)
    try:
        import httpx as _hx

        base_url = str(request.base_url).rstrip("/")
        async with _hx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{base_url}/api/v1/alerts")
            if r.status_code == 200:
                sys_data = r.json()
                for a in (sys_data.get("alerts") or [])[:15]:
                    items.append(
                        {
                            "id": a.get("id", ""),
                            "type": a.get("type", "alert"),
                            "title": a.get("title", ""),
                            "description": a.get("description", ""),
                            "severity": a.get("severity", "medium"),
                            "token": a.get("token") or "",
                            "chain": a.get("chain") or "",
                            "wallet": a.get("wallet") or "",
                            "amount_usd": _safe_float(a.get("amount_usd", 0)),
                            "created_at": a.get("created_at", now),
                            "source": "rmi_detector",
                            "metadata": a.get("metadata", {}),
                        }
                    )
    except Exception as e:
        logger.warning(f"Failed to fetch system alerts: {e}")

    # 3. Bad actors from rugmaps
    try:
        async with _hx.AsyncClient(timeout=5) as c:
            r2 = await c.get(f"{base_url}/api/v1/rugmaps/bad-actors")
            if r2.status_code == 200:
                ba_data = r2.json()
                for a in (ba_data.get("bad_actors") or [])[:10]:
                    items.append(
                        {
                            "id": f"bad_actor_{a.get('address', '')[:12]}",
                            "type": "bad_actor",
                            "title": f"Known Bad Actor: {a.get('label', 'Unknown')}",
                            "description": a.get(
                                "description", f"Flagged wallet {a.get('address', '')[:16]}... - {a.get('label', '')}"
                            ),
                            "severity": a.get("severity", "high"),
                            "token": "",
                            "chain": a.get("chain", ""),
                            "wallet": a.get("address", ""),
                            "amount_usd": 0,
                            "created_at": now,
                            "source": "rugmaps",
                            "metadata": {"label": a.get("label", ""), "tags": a.get("tags", [])},
                        }
                    )
    except Exception as e:
        logger.warning(f"Failed to fetch bad actors: {e}")

    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: (sev_order.get(x.get("severity", "low"), 4), x.get("created_at", now)), reverse=False)
    items = items[:limit]
    return {"items": items, "total": len(items), "updated_at": now}
