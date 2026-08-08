"""
Intelligence Router - n8n Webhook Receivers
=============================================
Endpoints that receive data from n8n workflows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger
from app.routers.intelligence_router.supabase import _supabase_is_configured, _supabase_request

if TYPE_CHECKING:
    from app.routers.intelligence_router.models import MarketUpdate

logger = get_logger(__name__)

router = APIRouter()


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
