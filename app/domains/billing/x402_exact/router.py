"""x402 exact — public router.

    POST /api/v1/x402/intel/scan        $0.005
    POST /api/v1/x402/security/wallet   $0.020
    POST /api/v1/x402/scanner/token     $0.010
    GET  /api/v1/x402/paid/catalog      free
    GET  /api/v1/x402/paid/ledger       admin key
    GET  /api/v1/x402/paid/health       free
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.domains.billing.x402_exact import ledger
from app.domains.billing.x402_exact.catalog import all_endpoints
from app.domains.billing.x402_exact.handlers import intel_scan, scanner_token, security_wallet

# Imported at runtime, not under TYPE_CHECKING: this module uses
# ``from __future__ import annotations``, and FastAPI builds request-body
# TypeAdapters from the endpoint annotations. A TYPE_CHECKING-only import
# leaves ``ForwardRef(ScanRequest)`` unresolvable, which made
# ``GET /openapi.json`` raise PydanticUserError.
from app.domains.billing.x402_exact.models import ScanRequest, WalletRequest


router = APIRouter(prefix="/api/v1/x402", tags=["x402-paid"])


@router.post("/intel/scan")
async def post_intel_scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
    """Paid token risk scan ($0.005) — 3 free calls/day per caller."""
    return await intel_scan(payload, request)


@router.post("/security/wallet")
async def post_security_wallet(payload: WalletRequest, request: Request) -> dict[str, Any]:
    """Paid wallet security intel ($0.02) — 2 free calls/day per caller."""
    return await security_wallet(payload, request)


@router.post("/scanner/token")
async def post_scanner_token(payload: ScanRequest, request: Request) -> dict[str, Any]:
    """Paid token security snapshot ($0.01) — 3 free calls/day per caller."""
    return await scanner_token(payload, request)


@router.get("/paid/catalog")
async def get_paid_catalog() -> dict[str, Any]:
    """Free: what is for sale, at what price, with its JSON schema."""
    return {
        "x402Version": 1,
        "endpoints": [
            {
                "handle": e.handle,
                "path": e.path,
                "priceUsd": e.usd,
                "priceAtoms": str(e.price_atoms),
                "freeDaily": e.free_daily,
                "description": e.description,
                "requestSchema": e.request_schema,
                "responseFields": list(e.response_fields),
            }
            for e in all_endpoints()
        ],
    }


@router.get("/paid/ledger")
async def get_paid_ledger(
    limit: int = 50,
    x_admin_key: str = Header(default=""),
) -> dict[str, Any]:
    """Admin: settled-call ledger (payer, resource, amount, tx hash)."""
    expected = os.getenv("ADMIN_API_KEY", "")
    if not expected or x_admin_key != expected:
        raise HTTPException(status_code=401, detail={"error": "admin_key_required"})
    rows = ledger.entries(limit=min(max(limit, 1), 500))
    return {"count": len(rows), "entries": rows}


@router.get("/paid/health")
async def get_paid_health() -> dict[str, Any]:
    """Free: facilitator reachability + catalog size."""
    import httpx

    base = (os.getenv("X402_FACILITATOR_URL") or "http://127.0.0.1:8001").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base}/v1/x402/health")
        facilitator_ok = response.status_code == 200
        status = response.status_code
    except (httpx.HTTPError, OSError):
        facilitator_ok, status = False, 0
    return {
        "facilitator": {"url": base, "reachable": facilitator_ok, "status": status},
        "endpoints": len(all_endpoints()),
        "network": "eip155:8453",
    }
