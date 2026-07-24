"""Public scanner endpoints - /api/v1/scanner/*.

Stub for unauthenticated token scanning. Real implementation will
run security checks and return risk assessment results.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

router = APIRouter(prefix="/scanner", tags=["scanner"])


class ScanRequest(BaseModel):
    """Request model for token scanning."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    address: str = Field(
        ...,
        min_length=26,
        max_length=44,
        pattern=r"^0x[a-fA-F0-9]{40}$",
    )
    depth: str = Field(
        default="quick",
        pattern=r"^(quick|standard|deep)$",
    )


class ScanResult(BaseModel):
    """Result of a token security scan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scan_id: str = Field(..., min_length=1, max_length=100)
    status: str = Field(
        ...,
        pattern=r"^(pending|running|completed|failed)$",
    )
    risk_score: int = Field(..., ge=0, le=100)
    risk_tier: str = Field(..., min_length=1, max_length=20)
    factors: list[str] = []


@router.post("/scan", response_model=ScanResult)
async def scan_token(request: ScanRequest) -> Any:
    """Run a security scan on a token address."""
    raise HTTPException(
        status_code=501,
        detail="Scanner not yet implemented - coming in v5.1",
    )
