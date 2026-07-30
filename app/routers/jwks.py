"""JWKS endpoint — serves the RSA public key for JWT validation.

All RMI services (walletpress, pryscraper, degenfeed) fetch the public key
from this endpoint to validate tokens issued by rmi-backend.

Endpoint:
    GET /.well-known/jwks.json  — JWKS format (preferred)
    GET /api/v1/auth/jwks       — alternative path
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.auth.jwt_validation import get_jwks

router = APIRouter(tags=["auth"])


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks_well_known() -> JSONResponse:
    """JWKS endpoint for JWT public key distribution.

    Services fetch this to dynamically discover the RSA public key
    for validating tokens issued by rmi-backend.
    """
    jwks = get_jwks()
    return JSONResponse(content=jwks)


@router.get("/api/v1/auth/jwks", summary="Get JWKS public key")
async def jwks_v1() -> JSONResponse:
    """JWKS endpoint — returns the RSA public key for JWT validation.

    Used by walletpress, pryscraper, and degenfeed to validate
    tokens issued by rmi-backend.
    """
    jwks = get_jwks()
    return JSONResponse(content=jwks)
