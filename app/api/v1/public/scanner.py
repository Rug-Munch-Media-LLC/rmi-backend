"""Public scanner endpoints — /api/v1/scanner/*.

Stub for unauthenticated token scans. Real implementation will
trigger the scanner pipeline (honeypot detection, flash loan checks,
oracle manipulation analysis).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/scanner", tags=["scanner"])


class ScanRequest(BaseModel):
    """Request to scan a token or wallet."""

    chain: str = "ethereum"
    address: str
    depth: str = "standard"  # "quick" | "standard" | "deep"


class ScanResult(BaseModel):
    """Result of a scan."""

    scan_id: str
    status: str  # "queued" | "running" | "completed" | "failed"
    risk_score: int | None = None
    risk_tier: str | None = None
    findings: list[str] = []


@router.post("/scan", response_model=ScanResult)
async def scan(req: ScanRequest) -> ScanResult:
    """Queue a token/wallet scan."""
    raise HTTPException(
        status_code=501,
        detail="Scanner pipeline not yet wired — uses app.domain.scanner (T06+)",
    )


@router.get("/result/{scan_id}", response_model=ScanResult)
async def get_scan_result(scan_id: str) -> ScanResult:
    """Get the result of a previously queued scan."""
    raise HTTPException(
        status_code=501,
        detail="Scan result retrieval not yet implemented",
    )


@router.get("/quick")
async def quick_scan(
    chain: str = Query("ethereum"),
    address: str = Query(...),
) -> dict[str, Any]:
    """Quick scan (free tier, no persistence)."""
    raise HTTPException(
        status_code=501,
        detail="Quick scan uses cached shield — see caching_shield module",
    )
