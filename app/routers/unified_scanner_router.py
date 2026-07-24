"""Unified Scanner Router v3 - matches WalletScanner v3 fields.

Dependency injection: ``UnifiedTokenScanner`` and ``UnifiedWalletScanner``
are injected via ``Depends()`` so they can be mocked in tests.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.dependencies import get_token_scanner, get_wallet_scanner
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger
from app.domains.scanner.unified_token_scanner import UnifiedTokenScanner
from app.domains.wallet.unified_wallet_scanner import UnifiedWalletScanner

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v2/scanner", tags=["scanner-v2"])

class TokenScanRequest(BaseModel):
    address: str
    chain: str = "solana"

class WalletScanRequest(BaseModel):
    address: str
    chain: str = "solana"

def _get_tier(request: Request) -> str:
    if request.headers.get("X-RMI-Key") == INTERNAL_API_KEY:
        return "admin"
    if request.headers.get("X-Payment-Verified") == "true":
        return "pro"
    return "free"

def _get_admin_key(request: Request) -> Any | None:
    return request.headers.get("X-Admin-Key") or request.headers.get("X-RMI-Key")

@router.post(
    "/token/scan",
    summary="Scan Token",
    description="Comprehensive token scan across 50+ security modules. Returns safety score, risk flags, green flags, and detailed module results.",
)
async def scan_token(
    request: Request,
    body: TokenScanRequest,
    scanner: UnifiedTokenScanner = Depends(get_token_scanner),
) -> dict[str, Any]:
    tier = _get_tier(request)
    admin_key = _get_admin_key(request)
    try:
        result = await scanner.scan(
            address=body.address, chain=body.chain, tier=tier, admin_key=admin_key
        )
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
            "data_source": "databus",
            "freshness": result.scanned_at,
            "fallback_used": result.confidence < 50,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e

@router.post(
    "/wallet/scan",
    summary="Scan Wallet",
    description="Comprehensive wallet scan with entity clustering, risk scoring, behavioral analysis, and RAG-based threat intelligence matching.",
)
async def scan_wallet(
    request: Request,
    body: WalletScanRequest,
    scanner: UnifiedWalletScanner = Depends(get_wallet_scanner),
) -> dict[str, Any]:
    tier = _get_tier(request)
    admin_key = _get_admin_key(request)
    try:
        result = await scanner.scan(
            address=body.address, chain=body.chain, tier=tier, admin_key=admin_key
        )
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
            "data_source": "databus",
            "freshness": result.scanned_at,
            "fallback_used": result.risk_score is None,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e
