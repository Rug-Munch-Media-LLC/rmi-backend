"""Unified Scanner Router v3 - matches WalletScanner v3 fields.

Dependency injection: ``UnifiedTokenScanner`` and ``UnifiedWalletScanner``
are injected via ``Depends()`` so they can be mocked in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.dependencies import get_token_scanner, get_wallet_scanner
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domains.scanner.unified import UnifiedTokenScanner
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


def _serialize_rag_context(rag_ctx: Any) -> dict[str, Any] | None:
    """Best-effort serialize RAGContext for the API response.

    Mirrors the compact shape the legacy scanner stores (see
    ``app/domains/scanner/service/scan.py``). Never raises — RAG
    enrichment is best-effort, so a broken RAG payload must not fail
    the scan response.
    """
    if rag_ctx is None:
        return None
    try:
        return {
            "similar_scams": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                    "doc_id": h.doc_id,
                }
                for h in rag_ctx.similar_scams[:5]
            ],
            "wallet_context": [
                {
                    "content": h.content[:200],
                    "score": round(h.final_score or h.score, 4),
                    "collection": h.collection,
                }
                for h in rag_ctx.wallet_context[:3]
            ],
            "market_signals": len(rag_ctx.market_context),
            "fusion_summary": rag_ctx.fusion_summary,
            "collections_searched": rag_ctx.collections_searched,
            "took_ms": rag_ctx.took_ms,
            "query_id": rag_ctx.query_id,
        }
    except Exception:
        return None


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
        result = await scanner.scan(address=body.address, chain=body.chain, tier=tier, admin_key=admin_key)
        # Track which sources were own data vs external API fallback
        # enrichment_sources has entries like "price:own", "price:coingecko",
        # "security:own_clickhouse", "security:goplus"
        own_data_modules = sum(1 for s in result.enrichment_sources if "own" in s.split(":")[-1].lower())
        fallback_modules = sum(
            1
            for s in result.enrichment_sources
            if "own" not in s.split(":")[-1].lower() and s.split(":")[-1] not in ("", "unknown")
        )
        total_with_source = len(result.enrichment_sources)
        fallback_used = total_with_source > 0 and fallback_modules >= own_data_modules

        return {
            "status": "ok",
            "token": result.token_address,
            "chain": result.chain,
            "name": result.name,
            "symbol": result.symbol,
            "price_usd": result.price_usd,
            "market_cap_usd": result.market_cap_usd,
            "liquidity_usd": result.liquidity_usd,
            "volume_24h_usd": result.volume_24h_usd,
            "age_hours": result.age_hours,
            "safety_score": result.safety_score,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "risk_flags": result.risk_flags,
            "green_flags": result.green_flags,
            "breakdown": result.breakdown,
            "evidence": result.evidence,
            "modules_run": result.modules_run,
            "scan_duration_ms": result.scan_duration_ms,
            "tier": result.tier,
            "scanned_at": result.scanned_at,
            "data_source": "databus",
            "freshness": result.scanned_at,
            "fallback_used": fallback_used,
            "data_sources": {
                "own_data_modules": own_data_modules,
                "external_fallback_modules": fallback_modules,
                "total_modules": len(result.modules_run),
            },
            "rag_context": _serialize_rag_context(result.rag_context),
            "news_sentiment": result.news_sentiment,
            "news_card": result.news_card,
            "sentinel": result.sentinel,
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
            "data_source": "databus",
            "freshness": result.scanned_at,
            "fallback_used": result.risk_score is None,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {str(e)[:200]}") from e
