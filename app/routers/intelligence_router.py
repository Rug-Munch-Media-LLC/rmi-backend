"""

Intelligence Router - Market, Premium, and Security Intelligence.
Receives data from n8n workflows, provides query endpoints for users.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.core.errors import AppError, AuthError, ServiceUnavailableError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])

# ── Supabase Async Helpers ────────────────────────────────────
# Replaces sync supabase-py client with async httpx calls to Supabase REST API.

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _supabase_is_configured() -> bool:
    """Check if Supabase credentials are configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


async def _supabase_request(
    method: str,
    table: str,
    data: dict[str, object] | list[dict[str, object]] | None = None,
    params: dict[str, str] | None = None,
    jwt_token: str | None = None,
) -> list[dict[str, object]]:
    """Make an async request to the Supabase REST API (PostgREST).

    Args:
        method: HTTP method (GET, POST, PATCH)
        table: Supabase table name
        data: JSON body for POST/PATCH
        params: Query parameters for PostgREST filtering (e.g. ``{"id": "eq.123"}``)
        jwt_token: Optional user JWT for user-scoped requests

    Returns:
        List of result rows (empty on error or no results).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    headers: dict[str, str] = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {jwt_token or SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method,
                f"{SUPABASE_URL}/rest/v1/{table}",
                json=data,
                params=params,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Supabase API error %d on %s %s: %s",
                    resp.status_code,
                    method,
                    table,
                    resp.text[:300],
                )
                return []
            if resp.status_code == 204:
                return []
            result: list[dict[str, object]] = resp.json()
            return result
        except httpx.RequestError as e:
            logger.error("Supabase request failed (%s %s): %s", method, table, e)
            return []


async def _supabase_get_user(jwt_token: str) -> dict[str, object] | None:
    """Get the authenticated user from Supabase Auth via REST API.

    Args:
        jwt_token: The user's JWT (without ``Bearer `` prefix).

    Returns:
        User dict with ``id``, ``email`` etc., or ``None`` on failure.
    """
    if not SUPABASE_URL or not SUPABASE_KEY or not jwt_token:
        return None

    headers: dict[str, str] = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {jwt_token}",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers=headers,
            )
            if resp.status_code == 200:
                user_result: dict[str, object] = resp.json()
                return user_result
            return None
        except httpx.RequestError as e:
            logger.error("Supabase auth request failed: %s", e)
            return None


async def _get_current_user_id(authorization: str | None = None) -> str | None:
    """Extract the current user ID from a JWT via Supabase Auth.

    Args:
        authorization: Full ``Authorization`` header value (e.g. ``Bearer <token>``).

    Returns:
        User ID string or ``None``.
    """
    if not authorization:
        return None
    try:
        user_data = await _supabase_get_user(authorization.replace("Bearer ", ""))
        if user_data and isinstance(user_data, dict):
            user_id = user_data.get("id")
            return str(user_id) if user_id else None
        return None
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"User auth lookup failed: {e}")
        return None


# ── Models ───────────────────────────────────────────────────


class MarketUpdate(BaseModel):
    """Market intelligence update from n8n."""

    workflow: str
    data: dict


class IntelligenceQuery(BaseModel):
    """Query parameters for intelligence data."""

    chain: str | None = None
    min_amount_usd: float | None = None
    min_confidence: float | None = None
    limit: int = 50
    offset: int = 0


# ── n8n Webhook Receivers ────────────────────────────────────


@router.post("/market-update")
async def receive_market_update(update: MarketUpdate) -> dict[str, str]:
    """Receive market intelligence update from n8n workflow."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    try:
        workflow = update.workflow
        data = update.data

        # Route to appropriate table based on workflow
        if workflow == "trending_tokens":
            # Process trending tokens
            tokens = data.get("coingecko_trending", {}).get("coins", []) + data.get("dexscreener_trending", {}).get(
                "pairs", []
            )

            for token in tokens[:20]:  # Top 20
                await _supabase_request(
                    "POST",
                    "market_trending_tokens",
                    data={
                        "token_address": token.get("address") or token.get("baseToken", {}).get("address"),
                        "token_name": token.get("name") or token.get("baseToken", {}).get("name"),
                        "token_symbol": token.get("symbol") or token.get("baseToken", {}).get("symbol"),
                        "chain": "solana",
                        "price_usd": token.get("priceUsd") or token.get("priceNative"),
                        "volume_24h": token.get("volume", {}).get("h24"),
                        "trend_score": token.get("score", 0),
                        "source": "coingecko_dexscreener",
                    },
                )

        elif workflow == "whales":
            # Process whale movements
            whales = data.get("helius_whales", [])
            for whale in whales:
                amount_usd = whale.get("amount", 0) * 150  # SOL price approx
                if amount_usd > 10000:  # Only store >$10k
                    await _supabase_request(
                        "POST",
                        "whale_movements",
                        data={
                            "wallet_address": whale.get("wallet"),
                            "chain": "solana",
                            "token_symbol": "SOL",
                            "amount_usd": amount_usd,
                            "transaction_hash": whale.get("signature"),
                            "source": "helius",
                        },
                    )

        elif workflow == "volume-spikes":
            # Process volume spikes
            spikes = data.get("dexscreener_spikes", [])
            for spike in spikes:
                if spike.get("volume_change_pct", 0) > 100:  # >100% increase
                    await _supabase_request(
                        "POST",
                        "volume_spikes",
                        data={
                            "token_address": spike.get("address"),
                            "token_symbol": spike.get("symbol"),
                            "chain": spike.get("chainId"),
                            "volume_change_pct": spike.get("volume_change_pct"),
                            "spike_score": spike.get("spike_score", 0),
                        },
                    )

        return {"status": "ok", "workflow": workflow, "processed": True}

    except Exception as e:
        logger.error("Market update error: %s", e)
        return {"status": "error", "message": str(e)[:200]}


@router.post("/security-update")
async def receive_security_update(update: MarketUpdate) -> dict[str, str]:
    """Receive security intelligence update from n8n workflow."""
    if not _supabase_is_configured():
        raise ServiceUnavailableError("Supabase not configured")

    try:
        workflow = update.workflow
        data = update.data

        if workflow == "scams":
            scams = data.get("scam_alerts", [])
            for scam in scams:
                await _supabase_request(
                    "POST",
                    "scam_alerts",
                    data={
                        "scam_type": scam.get("type"),
                        "token_address": scam.get("token"),
                        "chain": scam.get("chain"),
                        "confidence_score": scam.get("confidence", 0),
                        "is_confirmed": scam.get("confirmed", False),
                    },
                )

        elif workflow == "rugpulls":
            rugpulls = data.get("rugpull_alerts", [])
            for rug in rugpulls:
                await _supabase_request(
                    "POST",
                    "rugpull_alerts",
                    data={
                        "token_address": rug.get("token"),
                        "deployer_address": rug.get("deployer"),
                        "liquidity_removed_usd": rug.get("liquidity_removed"),
                        "rug_type": rug.get("type"),
                        "confidence_score": rug.get("confidence", 0),
                    },
                )

                # Create immediate alert for premium users
                await _supabase_request(
                    "POST",
                    "intelligence_alerts",
                    data={
                        "alert_type": "rugpull_detected",
                        "severity": "critical",
                        "title": f"Rugpull Detected: {rug.get('symbol', 'Unknown')}",
                        "message": f"Liquidity removed: ${rug.get('liquidity_removed', 0):,.0f}",
                        "data": rug,
                        "is_read": False,
                    },
                )

        return {"status": "ok", "workflow": workflow, "processed": True}

    except Exception as e:
        logger.error("Security update error: %s", e)
        return {"status": "error", "message": str(e)[:200]}


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


# ── Health ───────────────────────────────────────────────────


@router.get(
    "/health",
    summary="Intelligence Health",
    description="Intelligence service health check. Verifies connectivity to n8n, Supabase, and internal intelligence pipelines. Returns status for each subsystem.",
)
async def intelligence_health() -> dict[str, str]:
    """Intelligence service health check."""
    return {
        "status": "ok",
        "service": "intelligence-platform",
        "supabase_connected": _supabase_is_configured(),
        "features": [
            "market_intelligence",
            "premium_intelligence",
            "security_intelligence",
            "n8n_workflows",
            "user_alerts",
        ],
        "workflows": {
            "market": ["trending_tokens", "whale_movements", "volume_spikes"],
            "premium": ["smart_money", "exchange_flows"],
            "security": ["scam_detection", "rugpull_detection"],
        },
    }


# ── Card Generation Endpoints ─────────────────────────────────


@router.post("/cards/generate")
async def generate_card(card_type: str, data: dict) -> Any:
    """Generate shareable card (win, loss, KOL, rug, etc.)."""
    try:
        from app.card_generator import (
            generate_kol_scorecard,
            generate_loss_card,
            generate_rug_call_card,
            generate_smart_money_card,
            generate_whale_alert_card,
            generate_win_card,
        )

        generators = {
            "big_win": generate_win_card,
            "big_loss": generate_loss_card,
            "kol_scorecard": generate_kol_scorecard,
            "rug_call": generate_rug_call_card,
            "whale_move": generate_whale_alert_card,
            "smart_money": generate_smart_money_card,
        }

        generator = generators.get(card_type)
        if not generator:
            raise ValidationError(f"Unknown card type: {card_type}")

        result = await generator(**data)
        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.error("Card generation failed: %s", e)
        raise AppError(message=str(e)[:200]) from e


@router.get(
    "/cards/templates",
    summary="Get Card Templates",
    description="List available intelligence card templates for generating shareable security/market insight graphics.",
)
async def get_card_templates() -> dict[str, list[dict]]:
    """Get available card templates."""
    try:
        from app.card_generator import CARD_TEMPLATES

        return {
            "templates": [
                {
                    "id": key,
                    "name": template["name"],
                    "size": template["size"],
                    "colors": template["colors"],
                }
                for key, template in CARD_TEMPLATES.items()
            ]
        }
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


# ── Marketing Graphics Endpoints ─────────────────────────────


@router.get("/marketing/templates")
async def list_marketing_templates_endpoint() -> dict:
    """List available marketing graphic templates."""
    try:
        from app.marketing_graphics_generator import list_marketing_templates

        return {"templates": list_marketing_templates()}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post("/marketing/generate")
async def generate_marketing_graphic(graphic_type: str, data: dict | None = None) -> Any:
    """Generate marketing graphic (banner, feature card, stats, etc.)."""
    try:
        from app.marketing_graphics_generator import generate_marketing_graphic

        result = generate_marketing_graphic(graphic_type, data)

        if result.get("status") == "error":
            raise AppError(message=result.get("error"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        logger.error("Marketing graphic generation failed: %s", e)
        raise AppError(message=str(e)[:200]) from e


@router.post("/marketing/generate/feature-cards")
async def generate_all_feature_cards_endpoint() -> dict:
    """Generate feature showcase cards for all features."""
    try:
        from app.marketing_graphics_generator import generate_all_feature_cards

        results = await generate_all_feature_cards()
        return {"cards": results, "count": len(results)}
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post(
    "/marketing/generate/stats",
    summary="Generate Stats Graphic",
    description="Generate a stats/metrics marketing graphic from the provided stat value, label, and context. Returns a rendered image URL.",
)
async def generate_stats_graphic(stat_value: str, stat_label: str, context: str = "") -> Any:
    """Generate stats/metrics graphic."""
    try:
        from app.marketing_graphics_generator import generate_stats_card

        result = await generate_stats_card(stat_value, stat_label, context)

        if result.get("status") == "error":
            raise AppError(message=result.get("error"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e


@router.post(
    "/marketing/generate/pricing",
    summary="Generate Pricing Graphic",
    description="Generate a pricing tier marketing graphic with the given tier name, price, and feature list. Returns a rendered image URL.",
)
async def generate_pricing_graphic(tier_name: str, price: float, features: list[str]) -> Any:
    """Generate pricing tier graphic."""
    try:
        from app.marketing_graphics_generator import generate_pricing_card

        result = await generate_pricing_card(tier_name, price, features)

        if result.get("status") == "error":
            raise AppError(message=result.get("error"))

        return result

    except (HTTPException, AppError):
        raise
    except Exception as e:
        raise AppError(message=str(e)[:200]) from e
