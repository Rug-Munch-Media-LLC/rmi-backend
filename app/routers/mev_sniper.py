#!/usr/bin/env python3
"""#1 — MEV Sniper Dashboard. Real-time new pair signals + SENTINEL risk scoring.
Surfaces snipeable launches across all chains with safety filtering."""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/mev-sniper", tags=["mev-sniper"])

DEXSCREENER = "https://api.dexscreener.com/latest/dex/search"
BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
CHAINS = ["solana", "ethereum", "bsc", "base", "arbitrum", "polygon", "avalanche"]


class SnipeSignal(BaseModel):
    chain: str
    symbol: str
    address: str
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    age_minutes: int
    safety_score: int
    risk_flags: list[str]
    snipe_score: int = 0  # 0-100, composite attractiveness


async def _get_new_pairs(chain: str, max_age_min: int = 30) -> list[dict]:
    """Fetch newly created pairs from DexScreener."""
    pairs = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DEXSCREENER}?q={chain}")
            if resp.status_code != 200:
                return []
            all_pairs = resp.json().get("pairs", [])
            cutoff = (datetime.now(UTC) - timedelta(minutes=max_age_min)).timestamp() * 1000
            pairs = [p for p in all_pairs if p.get("pairCreatedAt", 0) > cutoff]
    except Exception:
        pass
    return pairs


def _compute_snipe_score(liquidity: float, volume: float, age_min: int, safety: int) -> int:
    """Composite attractiveness score for sniping."""
    score = 50
    if liquidity > 50000:
        score += 15
    elif liquidity > 10000:
        score += 8
    if volume > liquidity * 0.5:
        score += 15  # active trading
    if age_min < 5:
        score += 10  # fresh
    if age_min < 15:
        score += 5
    score += (safety - 50) * 0.3  # safety adjustment
    return min(100, max(0, int(score)))


@router.get("/signals")
async def get_snipe_signals(
    chain: str = "solana",
    max_age_min: int = Query(30, ge=1, le=120),
    min_liquidity: float = Query(5000, ge=0),
    min_safety: int = Query(30, ge=0, le=100),
    limit: int = Query(20, le=50),
):
    """Get real-time snipe signals for a chain. Sorted by snipe_score descending."""
    pairs = await _get_new_pairs(chain, max_age_min)
    signals: list[dict[str, Any]] = []

    for p in pairs[: limit * 2]:
        base = p.get("baseToken", {})
        addr = p.get("pairAddress", "") or base.get("address", "")
        liq = p.get("liquidity", {}).get("usd", 0) or 0
        if liq < min_liquidity:
            continue

        created_at = p.get("pairCreatedAt", 0) / 1000
        age_min = int((datetime.now(UTC).timestamp() - created_at) / 60) if created_at else 99

        # Quick safety check via internal API
        safety = 50
        flags: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{BACKEND}/api/v1/token/scan",
                    json={"token_address": addr, "chain": chain},
                    headers={"X-RMI-Key": "rmi-internal-2026"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    safety = data.get("safety_score", 50)
                    flags = data.get("risk_flags", [])
        except Exception:
            pass

        if safety < min_safety:
            continue

        snipe = _compute_snipe_score(liq, p.get("volume", {}).get("h24", 0) or 0, age_min, safety)
        signals.append(
            {
                "chain": chain,
                "symbol": base.get("symbol", "?"),
                "address": addr,
                "price_usd": float(p.get("priceUsd", 0) or 0),
                "liquidity_usd": liq,
                "volume_24h": float(p.get("volume", {}).get("h24", 0) or 0),
                "age_minutes": age_min,
                "safety_score": safety,
                "risk_flags": flags,
                "snipe_score": snipe,
            }
        )

    signals.sort(key=lambda s: s["snipe_score"], reverse=True)
    return {"chain": chain, "count": len(signals[:limit]), "signals": signals[:limit]}


@router.get("/chains")
async def list_sniper_chains():
    return {"chains": CHAINS}
