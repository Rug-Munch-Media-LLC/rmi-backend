"""
Intelligence Router - Market, Premium, and Security Intelligence.
Receives data from n8n workflows, provides query endpoints for users.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])


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


# ── Helpers ──────────────────────────────────────────────────


def _get_supabase():
    """Get Supabase client."""
    try:
        import os

        from supabase import create_client

        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")

        if not url or not key:
            return None

        return create_client(url, key)
    except ImportError:
        return None


async def _get_current_user_id(authorization: str = Header(None)) -> str | None:
    """Get current user ID from JWT."""
    if not authorization:
        return None

    try:
        import os

        from supabase import create_client

        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
        user = supabase.auth.get_user(authorization.replace("Bearer ", ""))
        return user.user.id if user and user.user else None
    except Exception:
        return None


# ── n8n Webhook Receivers ────────────────────────────────────


@router.post("/market-update")
async def receive_market_update(update: MarketUpdate):
    """Receive market intelligence update from n8n workflow."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

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
                supabase.table("market_trending_tokens").insert(
                    {
                        "token_address": token.get("address") or token.get("baseToken", {}).get("address"),
                        "token_name": token.get("name") or token.get("baseToken", {}).get("name"),
                        "token_symbol": token.get("symbol") or token.get("baseToken", {}).get("symbol"),
                        "chain": "solana",
                        "price_usd": token.get("priceUsd") or token.get("priceNative"),
                        "volume_24h": token.get("volume", {}).get("h24"),
                        "trend_score": token.get("score", 0),
                        "source": "coingecko_dexscreener",
                    }
                ).execute()

        elif workflow == "whales":
            # Process whale movements
            whales = data.get("helius_whales", [])
            for whale in whales:
                amount_usd = whale.get("amount", 0) * 150  # SOL price approx
                if amount_usd > 10000:  # Only store >$10k
                    supabase.table("whale_movements").insert(
                        {
                            "wallet_address": whale.get("wallet"),
                            "chain": "solana",
                            "token_symbol": "SOL",
                            "amount_usd": amount_usd,
                            "transaction_hash": whale.get("signature"),
                            "source": "helius",
                        }
                    ).execute()

        elif workflow == "volume-spikes":
            # Process volume spikes
            spikes = data.get("dexscreener_spikes", [])
            for spike in spikes:
                if spike.get("volume_change_pct", 0) > 100:  # >100% increase
                    supabase.table("volume_spikes").insert(
                        {
                            "token_address": spike.get("address"),
                            "token_symbol": spike.get("symbol"),
                            "chain": spike.get("chainId"),
                            "volume_change_pct": spike.get("volume_change_pct"),
                            "spike_score": spike.get("spike_score", 0),
                        }
                    ).execute()

        return {"status": "ok", "workflow": workflow, "processed": True}

    except Exception as e:
        logger.error(f"Market update error: {e}")
        return {"status": "error", "message": str(e)[:200]}


@router.post("/security-update")
async def receive_security_update(update: MarketUpdate):
    """Receive security intelligence update from n8n workflow."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        workflow = update.workflow
        data = update.data

        if workflow == "scams":
            scams = data.get("scam_alerts", [])
            for scam in scams:
                supabase.table("scam_alerts").insert(
                    {
                        "scam_type": scam.get("type"),
                        "token_address": scam.get("token"),
                        "chain": scam.get("chain"),
                        "confidence_score": scam.get("confidence", 0),
                        "is_confirmed": scam.get("confirmed", False),
                    }
                ).execute()

        elif workflow == "rugpulls":
            rugpulls = data.get("rugpull_alerts", [])
            for rug in rugpulls:
                supabase.table("rugpull_alerts").insert(
                    {
                        "token_address": rug.get("token"),
                        "deployer_address": rug.get("deployer"),
                        "liquidity_removed_usd": rug.get("liquidity_removed"),
                        "rug_type": rug.get("type"),
                        "confidence_score": rug.get("confidence", 0),
                    }
                ).execute()

                # Create immediate alert for premium users
                supabase.table("intelligence_alerts").insert(
                    {
                        "alert_type": "rugpull_detected",
                        "severity": "critical",
                        "title": f"Rugpull Detected: {rug.get('symbol', 'Unknown')}",
                        "message": f"Liquidity removed: ${rug.get('liquidity_removed', 0):,.0f}",
                        "data": rug,
                        "is_read": False,
                    }
                ).execute()

        return {"status": "ok", "workflow": workflow, "processed": True}

    except Exception as e:
        logger.error(f"Security update error: {e}")
        return {"status": "error", "message": str(e)[:200]}


# ── Market Intelligence Endpoints ────────────────────────────


@router.get("/market/trending")
async def get_trending_tokens(
    chain: str = Query("solana", description="Blockchain"),
    limit: int = Query(50, description="Number of results"),
):
    """Get trending tokens."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    result = (
        supabase.table("market_trending_tokens")
        .select("*")
        .eq("chain", chain)
        .order("trend_score", desc=True)
        .order("collected_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"tokens": result.data or []}


@router.get("/market/whales")
async def get_whale_movements(
    chain: str = Query("solana"),
    min_amount_usd: float = Query(10000),
    limit: int = Query(50),
):
    """Get recent whale movements."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    result = (
        supabase.table("whale_movements")
        .select("*")
        .eq("chain", chain)
        .gte("amount_usd", min_amount_usd)
        .order("collected_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"movements": result.data or []}


@router.get("/market/volume-spikes")
async def get_volume_spikes(
    chain: str = Query("solana"),
    min_change_pct: float = Query(100),
    limit: int = Query(50),
):
    """Get volume spike alerts."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    result = (
        supabase.table("volume_spikes")
        .select("*")
        .eq("chain", chain)
        .gte("volume_change_pct", min_change_pct)
        .order("spike_score", desc=True)
        .order("collected_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"spikes": result.data or []}


# ── Premium Intelligence Endpoints ───────────────────────────


@router.get("/premium/smart-money")
async def get_smart_money_activities(
    wallet_category: str | None = None,
    chain: str = Query("solana"),
    limit: int = Query(50),
):
    """Get smart money activities (premium feature)."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    query = (
        supabase.table("smart_money_activities")
        .select("*")
        .eq("chain", chain)
        .order("collected_at", desc=True)
        .limit(limit)
    )

    if wallet_category:
        query = query.eq("wallet_category", wallet_category)

    result = query.execute()

    return {"activities": result.data or []}


@router.get("/premium/exchange-flows")
async def get_exchange_flows(
    exchange_name: str | None = None,
    flow_type: str | None = None,
    limit: int = Query(50),
):
    """Get exchange flow analysis (premium feature)."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    query = supabase.table("exchange_flows").select("*").order("collected_at", desc=True).limit(limit)

    if exchange_name:
        query = query.eq("exchange_name", exchange_name)
    if flow_type:
        query = query.eq("flow_type", flow_type)

    result = query.execute()

    return {"flows": result.data or []}


# ── Security Intelligence Endpoints ──────────────────────────


@router.get("/security/scams")
async def get_scam_alerts(
    scam_type: str | None = None,
    confirmed_only: bool = Query(False),
    limit: int = Query(50),
):
    """Get scam alerts."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    query = (
        supabase.table("scam_alerts")
        .select("*")
        .order("confidence_score", desc=True)
        .order("collected_at", desc=True)
        .limit(limit)
    )

    if scam_type:
        query = query.eq("scam_type", scam_type)
    if confirmed_only:
        query = query.eq("is_confirmed", True)

    result = query.execute()

    return {"scams": result.data or []}


@router.get("/security/rugpulls")
async def get_rugpull_alerts(
    limit: int = Query(50),
):
    """Get rugpull alerts (real-time)."""
    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    result = (
        supabase.table("rugpull_alerts")
        .select("*")
        .order("confidence_score", desc=True)
        .order("collected_at", desc=True)
        .limit(limit)
        .execute()
    )

    return {"rugpulls": result.data or []}


# ── User Intelligence Alerts ─────────────────────────────────


@router.get("/me/alerts")
async def get_my_alerts(
    unread_only: bool = Query(False),
    severity: str | None = None,
    limit: int = Query(50),
    authorization: str = Header(None),
):
    """Get my intelligence alerts."""
    user_id = await _get_current_user_id(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    query = (
        supabase.table("intelligence_alerts")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if unread_only:
        query = query.eq("is_read", False)
    if severity:
        query = query.eq("severity", severity)

    result = query.execute()

    # Mark as read if unread_only
    if unread_only and result.data:
        supabase.table("intelligence_alerts").update({"is_read": True, "read_at": datetime.now(UTC).isoformat()}).eq(
            "user_id", user_id
        ).eq("is_read", False).execute()

    return {"alerts": result.data or []}


@router.post("/me/alerts/{alert_id}/dismiss")
async def dismiss_alert(
    alert_id: str,
    authorization: str = Header(None),
):
    """Dismiss an intelligence alert."""
    user_id = await _get_current_user_id(authorization)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    result = (
        supabase.table("intelligence_alerts")
        .update({"is_dismissed": True})
        .eq("id", alert_id)
        .eq("user_id", user_id)
        .execute()
    )

    return {"status": "ok", "dismissed": bool(result.data)}


# ── Health ───────────────────────────────────────────────────


@router.get("/health")
async def intelligence_health():
    """Intelligence service health check."""
    supabase = _get_supabase()
    return {
        "status": "ok",
        "service": "intelligence-platform",
        "supabase_connected": supabase is not None,
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
async def generate_card(card_type: str, data: dict):
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
            raise HTTPException(status_code=400, detail=f"Unknown card type: {card_type}")

        result = await generator(**data)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Card generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/cards/templates")
async def get_card_templates():
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
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Marketing Graphics Endpoints ─────────────────────────────


@router.get("/marketing/templates")
async def list_marketing_templates_endpoint():
    """List available marketing graphic templates."""
    try:
        from app.marketing_graphics_generator import list_marketing_templates

        return {"templates": list_marketing_templates()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/marketing/generate")
async def generate_marketing_graphic(graphic_type: str, data: dict | None = None):
    """Generate marketing graphic (banner, feature card, stats, etc.)."""
    try:
        from app.marketing_graphics_generator import generate_marketing_graphic

        result = generate_marketing_graphic(graphic_type, data)

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Marketing graphic generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/marketing/generate/feature-cards")
async def generate_all_feature_cards_endpoint():
    """Generate feature showcase cards for all features."""
    try:
        from app.marketing_graphics_generator import generate_all_feature_cards

        results = await generate_all_feature_cards()
        return {"cards": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/marketing/generate/stats")
async def generate_stats_graphic(stat_value: str, stat_label: str, context: str = ""):
    """Generate stats/metrics graphic."""
    try:
        from app.marketing_graphics_generator import generate_stats_card

        result = await generate_stats_card(stat_value, stat_label, context)

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/marketing/generate/pricing")
async def generate_pricing_graphic(tier_name: str, price: float, features: list[str]):
    """Generate pricing tier graphic."""
    try:
        from app.marketing_graphics_generator import generate_pricing_card

        result = await generate_pricing_card(tier_name, price, features)

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error"))

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e
