"""V1 scan endpoints — /api/v1/scan/*.

Token and wallet scanning. Redirects to existing scanner handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/scan", tags=["scan-v1"])


@router.post("/token")
async def scan_token(address: str, chain: str = "ethereum"):
    """Scan a token for risks.

    Full implementation at /api/v2/scanner/token/scan
    (mounted from app.routers.unified_scanner_router).
    """
    raise HTTPException(
        status_code=501,
        detail="Token scan — use POST /api/v2/scanner/token/scan",
    )


@router.post("/wallet")
async def scan_wallet(address: str, chain: str = "ethereum"):
    """Scan a wallet for risks.

    Full implementation at /api/v2/scanner/wallet/scan
    (mounted from app.routers.unified_scanner_router).
    """
    raise HTTPException(
        status_code=501,
        detail="Wallet scan — use POST /api/v2/scanner/wallet/scan",
    )
