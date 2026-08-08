#!/usr/bin/env python3
"""#11 - Liquidity Pool Health Monitor. Tracks LPs across DEXes on all DataBus chains.

Alerts on LP withdrawals, depletion, single-holder dominance."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/lp-health", tags=["lp-health-monitor"])

DATABUS = settings.databus_url
BACKEND = settings.backend_url
TG_BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Tracked LPs (in-memory, Redis in prod)
_tracked_lps: dict[str, dict[str, Any]] = {}  # lp_id -> tracking config


class LPHealthResult(BaseModel):
    lp_address: str
    chain: str
    token_pair: str
    total_liquidity_usd: float
    liquidity_change_24h: float
    largest_holder_pct: float
    holder_count: int
    lp_locked_pct: float
    health_score: int  # 0-100
    alerts: list[str]


def _compute_health(ll: dict[str, Any]) -> tuple[int, list[str]]:
    """Compute LP health score from metrics."""
    score = 100
    alerts: list[str] = []

    if ll.get("largest_holder_pct", 0) > 50:
        score -= 40
        alerts.append(f"Single holder dominates {ll['largest_holder_pct']:.0f}% of LP")
    elif ll.get("largest_holder_pct", 0) > 30:
        score -= 20
        alerts.append(f"Concentrated LP: largest holder has {ll['largest_holder_pct']:.0f}%")

    if ll.get("lp_locked_pct", 100) < 50:
        score -= 30
        alerts.append(f"LP only {ll['lp_locked_pct']:.0f}% locked")
    elif ll.get("lp_locked_pct", 100) < 80:
        score -= 10

    change = ll.get("liquidity_change_24h", 0)
    if change < -50:
        score -= 35
        alerts.append(f"Massive LP drain: {change:.0f}% in 24h")
    elif change < -20:
        score -= 15
        alerts.append(f"Significant LP decrease: {change:.0f}% in 24h")

    if ll.get("total_liquidity_usd", 0) < 10000:
        score -= 10
        alerts.append(f"Low liquidity: ${ll['total_liquidity_usd']:,.0f}")

    return max(0, score), alerts


@router.get("/check/{lp_address}")
async def check_lp_health(lp_address: str, chain: str = Query("ethereum")) -> dict[str, Any] | dict[str, str]:
    """Check health of a specific liquidity pool."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DATABUS}/{chain}/pool/{lp_address}")
            if resp.status_code != 200:
                return {"error": f"LP not found on {chain}"}
            lp_data = resp.json().get("data", {})

        health, alerts = _compute_health(lp_data)
        return {
            "lp_address": lp_address,
            "chain": chain,
            "token_pair": lp_data.get("pair", f"{chain}/?"),
            "total_liquidity_usd": lp_data.get("liquidity_usd", 0) or 0,
            "liquidity_change_24h": lp_data.get("change_24h_pct", 0) or 0,
            "largest_holder_pct": lp_data.get("top_holder_pct", 0) or 0,
            "holder_count": lp_data.get("holder_count", 0) or 0,
            "lp_locked_pct": lp_data.get("locked_pct", 0) or 0,
            "health_score": health,
            "alerts": alerts,
        }
    except Exception as e:
        return {"lp_address": lp_address, "chain": chain, "error": str(e)}


@router.post("/track")
async def track_lp(lp_address: str, chain: str = Query("ethereum"), notify_tg: bool = Query(True)) -> dict[str, Any]:
    """Start tracking an LP for health changes."""
    lp_id = f"{chain}:{lp_address}"
    _tracked_lps[lp_id] = {
        "lp_address": lp_address,
        "chain": chain,
        "notify": notify_tg,
        "added_at": datetime.now(UTC).isoformat(),
    }
    return {"id": lp_id, "status": "tracking"}


@router.delete("/track/{lp_id}")
async def untrack_lp(lp_id: str) -> dict[str, Any]:
    """Stop tracking an LP."""
    _tracked_lps.pop(lp_id, None)
    return {"id": lp_id, "status": "removed"}


@router.get(
    "/tracked",
    summary="List Tracked LPs",
    description="List all liquidity pools currently being monitored for health changes.",
)
async def list_tracked() -> dict[str, list[Any]]:
    return {"tracked": list(_tracked_lps.values()), "count": len(_tracked_lps)}


@router.get("/unhealthy")
async def find_unhealthy_pools(
    chain: str = Query("ethereum"),
    limit: int = Query(10, le=20),
) -> dict[str, Any]:
    """Scan for unhealthy pools: low locked LP, LP drain, single-holder dominance."""
    unhealthy: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{DATABUS}/{chain}/pools/top?sort=risk&limit={limit * 3}")
            if resp.status_code == 200:
                pools = resp.json().get("data", {}).get("pools", [])
                for p in pools:
                    health, alerts = _compute_health(p)
                    if health < 70:
                        unhealthy.append(
                            {
                                "lp_address": p.get("address", ""),
                                "chain": chain,
                                "pair": p.get("pair", "?"),
                                "health_score": health,
                                "alerts": alerts,
                            }
                        )
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Pool health check failed: {e}")

    unhealthy.sort(key=lambda p: p["health_score"])
    return {"chain": chain, "unhealthy_pools": unhealthy[:limit], "total_unhealthy": len(unhealthy)}
