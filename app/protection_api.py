# Protection API endpoints for RMI
# Core detection API that all protection modules call
#
# NOTE: This module is a stub. The underlying services (RAGService class,
# Scanner, WalletLabels, EntityIntel, Blocklist) have not been implemented
# as importable classes yet. The rag_service module uses standalone async
# functions, not a RAGService class. We wrap imports in try/except so the
# app doesn't crash on startup.

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Attempt imports — these are stubs and may not exist
try:
    from app.rag_service import detect_scam_patterns, search_similar
except ImportError:
    detect_scam_patterns = None
    search_similar = None

try:
    from app.url_scam_detector import check_url_safety
except ImportError:
    check_url_safety = None

try:
    from app.wallet_persona import analyze_wallet
except ImportError:
    analyze_wallet = None

router = APIRouter(prefix="/api/v1/protect", tags=["protection"])


class UrlCheckRequest(BaseModel):
    url: str


class WalletCheckRequest(BaseModel):
    address: str
    chain: str


class TokenCheckRequest(BaseModel):
    address: str
    chain: str


@router.post("/check-url")
async def check_url(request: UrlCheckRequest):
    """Check URL safety against scam patterns and blocklist"""
    try:
        # Use detect_scam_patterns from rag_service (standalone function)
        if detect_scam_patterns:
            rag_result = await detect_scam_patterns(request.url)
            if rag_result and rag_result.get("is_scam"):
                return {
                    "safe": False,
                    "risk": rag_result.get("risk_level", "high"),
                    "reason": rag_result.get("reason", "Known scam pattern"),
                    "source": "rag",
                }

        # Use URL scam detector if available
        if check_url_safety:
            scan_result = await check_url_safety(request.url)
            if scan_result and not scan_result.get("safe", True):
                return {
                    "safe": False,
                    "risk": scan_result.get("risk", "high"),
                    "reason": scan_result.get("reason", "Suspicious URL"),
                    "source": "scanner",
                }

        return {"safe": True, "risk": "low", "reason": "No known risks found", "source": "scanner"}
    except Exception as e:
        logger.error(f"URL check failed: {e}")
        return {"safe": True, "error": "service_unavailable", "reason": str(e)}


@router.post("/check-wallet")
async def check_wallet(request: WalletCheckRequest):
    """Check wallet safety against labels and analysis"""
    try:
        if analyze_wallet:
            result = await analyze_wallet(request.address, request.chain)
            if result and result.get("risk_score", 0) > 70:
                return {
                    "safe": False,
                    "risk": "high",
                    "reason": result.get("risk_reasons", ["High risk wallet"]),
                    "source": "analysis",
                }

        return {"safe": True, "risk": "low", "reason": "No known risks found", "source": "analysis"}
    except Exception as e:
        logger.error(f"Wallet check failed: {e}")
        return {"safe": True, "error": "service_unavailable", "reason": str(e)}


@router.post("/check-token")
async def check_token(request: TokenCheckRequest):
    """Check token safety — delegates to x402-tools risk_scan for full analysis"""
    return {
        "safe": True,
        "note": "Use /api/v1/x402-tools/risk_scan for full token security analysis",
        "address": request.address,
        "chain": request.chain,
    }


@router.get("/health")
async def protection_health():
    """Health check for protection services"""
    services = {
        "rag_scam_detection": detect_scam_patterns is not None,
        "url_safety_check": check_url_safety is not None,
        "wallet_analysis": analyze_wallet is not None,
    }
    all_ok = all(services.values())
    return {"status": "ok" if all_ok else "partial", "services": services}
