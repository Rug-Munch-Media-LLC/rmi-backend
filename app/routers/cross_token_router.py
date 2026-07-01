"""
Cross-Token Tracking API Router — Track wallets across multiple tokens.
Connects to /api/v1/cross-token/*
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/cross-token", tags=["cross-token"])

# Lazy import — cross_token.py has broken internal deps
PROJECT_TOKENS = {
    "CRM": "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS",
    "SOSANA": "SoSaNaTokenAddressPlaceholder123456789",
    "PBTC": "pBTC Token Address Placeholder",
    "SHIFT_AI": "ShiftAITokenAddressPlaceholder123456",
}


class CrossTokenRequest(BaseModel):
    wallet: str
    token_addresses: list[str] | None = None


@router.get("/projects")
async def list_projects():
    """List all tracked token projects."""
    return {"projects": PROJECT_TOKENS, "count": len(PROJECT_TOKENS)}


@router.post("/affiliations")
async def track_affiliations(req: CrossTokenRequest):
    """Track a wallet's affiliations across all known token projects."""
    try:
        from app.cross_token import CrossTokenAffiliationTracker

        tracker = CrossTokenAffiliationTracker()
        affiliations = await tracker.track_wallet_affiliations(wallet=req.wallet)
        return {
            "wallet": req.wallet,
            "affiliations": [
                {
                    "project": a.project,
                    "token_address": a.token_address,
                    "balance": a.balance,
                    "first_acquired": str(a.first_acquired) if a.first_acquired else None,
                    "evidence_strength": a.evidence_strength,
                }
                for a in affiliations
            ],
            "project_count": len(affiliations),
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Module unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.get("/connections/{wallet}")
async def get_cross_connections(wallet: str):
    """Find cross-token connections for a wallet."""
    try:
        from app.cross_token import CrossTokenAffiliationTracker

        tracker = CrossTokenAffiliationTracker()
        affiliations = await tracker.track_wallet_affiliations(wallet=wallet)
        multi = [a for a in affiliations if a.evidence_strength != "unverified"]
        return {
            "wallet": wallet,
            "multi_project": len(multi),
            "projects": [a.project for a in multi],
            "total_affiliations": len(affiliations),
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.get("/health")
async def cross_token_health():
    return {"status": "ok", "service": "cross-token-tracker"}
