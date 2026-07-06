"""Unified Scanner Router v3 - matches WalletScanner v3 fields."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.unified_token_scanner import get_token_scanner
from app.unified_wallet_scanner import get_wallet_scanner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["scanner-v2"])


class TokenScanRequest(BaseModel):
    address: str
    chain: str = "solana"


class WalletScanRequest(BaseModel):
    address: str
    chain: str = "solana"


def _get_tier(request):
    if request.headers.get("X-RMI-Key") == "rmi-internal-2026":
        return "admin"
    if request.headers.get("X-Payment-Verified") == "true":
        return "pro"
    return "free"


def _get_admin_key(request):
    return request.headers.get("X-Admin-Key") or request.headers.get("X-RMI-Key")


@router.post("/token/scan")
async def scan_token(request: Request, body: TokenScanRequest):
    tier = _get_tier(request)
    admin_key = _get_admin_key(request)
    try:
        scanner = get_token_scanner()
        result = await scanner.scan(address=body.address, chain=body.chain, tier=tier, admin_key=admin_key)
        return {
            "status": "ok",
            "token": result.token_address,
            "chain": result.chain,
            "name": result.name,
            "symbol": result.symbol,
            "price_usd": result.price_usd,
            "safety_score": result.safety_score,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "risk_flags": result.risk_flags,
            "green_flags": result.green_flags,
            "modules_run": result.modules_run,
            "scan_duration_ms": result.scan_duration_ms,
            "tier": result.tier,
            "scanned_at": result.scanned_at,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e


@router.post("/wallet/scan")
async def scan_wallet(request: Request, body: WalletScanRequest):
    tier = _get_tier(request)
    admin_key = _get_admin_key(request)
    try:
        scanner = get_wallet_scanner()
        result = await scanner.scan(address=body.address, chain=body.chain, tier=tier, admin_key=admin_key)
        return {
            "status": "ok",
            "address": result.address,
            "chain": result.chain,
            "labels": result.labels,
            "entity_name": result.entity_name,
            "entity_type": result.entity_type,
            "persona": result.persona,
            "persona_confidence": result.persona_confidence,
            "total_value_usd": result.total_value_usd,
            "token_count": result.token_count,
            "scam_tokens_held": result.scam_tokens_held,
            "scam_exposure_pct": result.scam_exposure_pct,
            "risk_score": result.risk_score,
            "risk_flags": result.risk_flags,
            "rag_matches": len(result.rag_matches),
            "behavior_patterns": result.behavior_patterns,
            "smart_money_overlap": result.smart_money_overlap,
            "risk_forecast": result.risk_forecast,
            "modules_run": result.modules_run,
            "scan_duration_ms": result.scan_duration_ms,
            "tier": result.tier,
            "scanned_at": result.scanned_at,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e
