"""
Intelligence Router - Query Endpoints
======================================
Market intelligence, premium, security, and alerts query endpoints.
"""

from __future__ import annotations
from typing import Any

from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query

from app.core.errors import AuthError, ServiceUnavailableError
from app.core.logging import get_logger
from app.routers.intelligence_router.supabase import (
    _get_current_user_id,
    _supabase_is_configured,
    _supabase_request,
)

logger = get_logger(__name__)

router = APIRouter()


# ── Market Intelligence Endpoints ─────────────────────────────


@router.get(
    "/market/trending",
    summary="Get Trending Tokens",
    description="Fetch trending tokens across multiple chains from DexScreener and CoinGecko. Returns tokens ranked by volume and social momentum. Supports chain and limit filtering.",
)
async def get_trending_tokens(
    chain: str = Query("solana", description="Blockchain"),
    limit: int = Query(50, description="Number of results"),
) -> dict:
    """Get trending tokens."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    result = await _supabase_request(
        "GET",
        "market_trending_tokens",
        params={
            "select": "*",
            "chain": f"eq.{chain}",
            "order": "trend_score.desc,collected_at.desc",
            "limit": str(limit),
        },
    )

    return {"tokens": result or []}


@router.get(
    "/market/whales",
    summary="Get Whale Movements",
    description="Detect large wallet transactions and whale movements across supported chains. Returns recent high-value transfers with entity labels when available.",
)
async def get_whale_movements(
    chain: str = Query("solana"),
    min_amount_usd: float = Query(10000),
    limit: int = Query(50),
) -> dict:
    """Get recent whale movements."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    result = await _supabase_request(
        "GET",
        "whale_movements",
        params={
            "select": "*",
            "chain": f"eq.{chain}",
            "amount_usd": f"gte.{min_amount_usd}",
            "order": "collected_at.desc",
            "limit": str(limit),
        },
    )

    return {"movements": result or []}


@router.get(
    "/market/volume-spikes",
    summary="Get Volume Spikes",
    description="Alert on sudden volume spikes indicating coordinated trading activity or potential pump-and-dump events. Configurable threshold via query params.",
)
async def get_volume_spikes(
    chain: str = Query("solana"),
    min_change_pct: float = Query(100),
    limit: int = Query(50),
) -> dict:
    """Get volume spike alerts."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    result = await _supabase_request(
        "GET",
        "volume_spikes",
        params={
            "select": "*",
            "chain": f"eq.{chain}",
            "volume_change_pct": f"gte.{min_change_pct}",
            "order": "spike_score.desc,collected_at.desc",
            "limit": str(limit),
        },
    )

    return {"spikes": result or []}


# ── Premium Intelligence Endpoints ───────────────────────────


@router.get("/premium/smart-money")
async def get_smart_money_activities(
    wallet_category: str | None = None,
    chain: str = Query("solana"),
    limit: int = Query(50),
) -> dict:
    """Get smart money activities (premium feature)."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    params: dict[str, str] = {
        "select": "*",
        "chain": f"eq.{chain}",
        "order": "collected_at.desc",
        "limit": str(limit),
    }
    if wallet_category:
        params["wallet_category"] = f"eq.{wallet_category}"

    result = await _supabase_request("GET", "smart_money_activities", params=params)

    return {"activities": result or []}


@router.get("/premium/exchange-flows")
async def get_exchange_flows(
    exchange_name: str | None = None,
    flow_type: str | None = None,
    limit: int = Query(50),
) -> dict:
    """Get exchange flow analysis (premium feature)."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    params: dict[str, str] = {
        "select": "*",
        "order": "collected_at.desc",
        "limit": str(limit),
    }
    if exchange_name:
        params["exchange_name"] = f"eq.{exchange_name}"
    if flow_type:
        params["flow_type"] = f"eq.{flow_type}"

    result = await _supabase_request("GET", "exchange_flows", params=params)

    return {"flows": result or []}


# ── Security Intelligence Endpoints ──────────────────────────


@router.get(
    "/security/scams",
    summary="Get Scam Alerts",
    description="Real-time scam alerts aggregated from community reports, on-chain analysis, and threat intel feeds. Includes contract addresses, scam type classification, and severity ratings.",
)
async def get_scam_alerts(
    scam_type: str | None = None,
    confirmed_only: bool = Query(False),
    limit: int = Query(50),
) -> dict:
    """Get scam alerts."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    params: dict[str, str] = {
        "select": "*",
        "order": "confidence_score.desc,collected_at.desc",
        "limit": str(limit),
    }
    if scam_type:
        params["scam_type"] = f"eq.{scam_type}"
    if confirmed_only:
        params["is_confirmed"] = "eq.true"

    result = await _supabase_request("GET", "scam_alerts", params=params)

    return {"scams": result or []}


@router.get(
    "/security/rugpulls",
    summary="Get Rugpull Alerts",
    description="Real-time rugpull alerts with liquidity analysis, deployer history, and honeypot detection. Flags tokens showing rugpull indicators within the configured time window.",
)
async def get_rugpull_alerts(
    limit: int = Query(50),
) -> dict:
    """Get rugpull alerts (real-time)."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    result = await _supabase_request(
        "GET",
        "rugpull_alerts",
        params={
            "select": "*",
            "order": "confidence_score.desc,collected_at.desc",
            "limit": str(limit),
        },
    )

    return {"rugpulls": result or []}


# ── User Intelligence Alerts ─────────────────────────────────


@router.get(
    "/me/alerts",
    summary="Get My Alerts",
    description="Get intelligence alerts for the authenticated user. Returns personalized alerts based on watched wallets, tokens, and configured notification preferences.",
)
async def get_my_alerts(
    unread_only: bool = Query(False),
    severity: str | None = None,
    limit: int = Query(50),
    authorization: str = Header(None),
) -> dict:
    """Get my intelligence alerts."""
    user_id = await _get_current_user_id(authorization)
    if not user_id:
        raise AuthError("Not authenticated")

    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    params: dict[str, str] = {
        "select": "*",
        "user_id": f"eq.{user_id}",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    if unread_only:
        params["is_read"] = "eq.false"
    if severity:
        params["severity"] = f"eq.{severity}"

    result = await _supabase_request("GET", "intelligence_alerts", params=params)

    # Mark as read if unread_only
    if unread_only and result:
        await _supabase_request(
            "PATCH",
            "intelligence_alerts",
            data={
                "is_read": True,
                "read_at": datetime.now(UTC).isoformat(),
            },
            params={
                "user_id": f"eq.{user_id}",
                "is_read": "eq.false",
            },
        )

    return {"alerts": result or []}


@router.post(
    "/me/alerts/{alert_id}/dismiss",
    summary="Dismiss Alert",
    description="Dismiss a specific intelligence alert. The alert will be removed from the active queue but retained in history for audit.",
)
async def dismiss_alert(
    alert_id: str,
    authorization: str = Header(None),
) -> dict[str, str]:
    """Dismiss an intelligence alert."""
    user_id = await _get_current_user_id(authorization)
    if not user_id:
        raise AuthError("Not authenticated")

    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    result = await _supabase_request(
        "PATCH",
        "intelligence_alerts",
        data={"is_dismissed": True},
        params={
            "id": f"eq.{alert_id}",
            "user_id": f"eq.{user_id}",
        },
    )

    return {"status": "ok", "dismissed": len(result) > 0}
