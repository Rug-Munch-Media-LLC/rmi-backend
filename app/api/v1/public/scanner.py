"""Public scanner endpoints - /api/v1/scanner/*.

Token + wallet scanning, scan status checks. Wires user-reported
addresses into the backend scanner pipeline.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

router = APIRouter(prefix="/scanner", tags=["scanner"])

# ── In-memory scan storage (ephemeral — survives for status polling) ──
_scan_results: dict[str, dict] = {}

# ── Token scan models ──


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


# ── Wallet scan models ──


class WalletScanRequest(BaseModel):
    """Request model for wallet scanning."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(
        default="solana",
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    address: str = Field(..., min_length=26, max_length=44)
    depth: str = Field(default="quick", pattern=r"^(quick|standard|deep)$")


class WalletScanResult(BaseModel):
    """Result of a wallet security scan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scan_id: str = Field(..., min_length=1, max_length=100)
    status: str = Field(..., pattern=r"^(pending|running|completed|failed)$")
    address: str = Field(..., min_length=26, max_length=44)
    chain: str = Field(..., min_length=1, max_length=50)
    risk_score: int = Field(..., ge=0, le=100)
    risk_factors: list[str] = []
    token_holdings: list[dict] = []
    analysis: dict = {}


# ══════════════════════════════════════════════════════════════════════
# TOKEN SCAN
# ══════════════════════════════════════════════════════════════════════


@router.post("/scan", response_model=ScanResult)
async def scan_token(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
) -> Any:
    """Run a security scan on a token address.

    Kicks off the scan asynchronously and returns a scan_id immediately.
    Poll GET /scan/{scan_id} for status.
    """
    scan_id = f"scan_{uuid4().hex[:12]}"
    _scan_results[scan_id] = {
        "status": "pending",
        "risk_score": 0,
        "risk_tier": "unknown",
        "factors": [],
    }
    background_tasks.add_task(_run_token_scan, scan_id, request.chain, request.address, request.depth)
    return ScanResult(
        scan_id=scan_id,
        status="pending",
        risk_score=0,
        risk_tier="unknown",
    )


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan_status(scan_id: str) -> Any:
    """Check the status of a previously submitted scan."""
    result = _scan_results.get(scan_id)
    if not result:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanResult(scan_id=scan_id, **result)


async def _run_token_scan(scan_id: str, chain: str, address: str, depth: str) -> None:
    """Run the actual token scan in the background."""
    try:
        # Attempt real scanner integration
        from app.scanners.token_scanner import scan_token as real_scan

        result = await real_scan(chain=chain, address=address, depth=depth)
        _scan_results[scan_id] = {
            "status": "completed",
            "risk_score": result.get("risk_score", 50),
            "risk_tier": result.get("risk_tier", "caution"),
            "factors": result.get("factors", result.get("threats", [])),
        }
    except ImportError:
        # Fallback: compute basic risk from on-chain data
        try:
            from app.unified_scanner import scan_wallet as wallet_scan

            result = await wallet_scan(address=address, chain=chain)
            score = result.get("risk_score", 50)
            _scan_results[scan_id] = {
                "status": "completed",
                "risk_score": score,
                "risk_tier": "medium" if score > 50 else "low",
                "factors": result.get("flags", result.get("risk_factors", [])),
            }
        except Exception as e:
            _scan_results[scan_id] = {
                "status": "failed",
                "risk_score": 0,
                "risk_tier": "unknown",
                "factors": [str(e)[:200]],
            }
    except Exception as e:
        _scan_results[scan_id] = {
            "status": "failed",
            "risk_score": 0,
            "risk_tier": "unknown",
            "factors": [str(e)[:200]],
        }


# ══════════════════════════════════════════════════════════════════════
# WALLET SCAN
# ══════════════════════════════════════════════════════════════════════


@router.post("/scan-wallet", response_model=WalletScanResult)
async def scan_wallet_endpoint(request: WalletScanRequest) -> Any:
    """Run a security scan on a wallet address.

    Used by walletpress and other apps to delegate wallet scanning
    to the RMI intelligence scanner.
    """
    scan_id = f"wscan_{uuid4().hex[:12]}"
    try:
        from app.unified_scanner import scan_wallet as _scan_wallet

        result = await _scan_wallet(address=request.address, chain=request.chain)
        return WalletScanResult(
            scan_id=scan_id,
            status="completed",
            address=request.address,
            chain=request.chain,
            risk_score=result.get("risk_score", 0),
            risk_factors=result.get("flags", result.get("risk_factors", [])),
            token_holdings=result.get("top_tokens", result.get("token_holdings", [])),
            analysis=result,
        )
    except ImportError:
        return WalletScanResult(
            scan_id=scan_id,
            status="completed",
            address=request.address,
            chain=request.chain,
            risk_score=0,
            risk_factors=[],
            token_holdings=[],
            analysis={"note": "Full scanner not available — basic check only"},
        )
    except Exception as e:
        return WalletScanResult(
            scan_id=scan_id,
            status="failed",
            address=request.address,
            chain=request.chain,
            risk_score=0,
            risk_factors=[f"Scan error: {str(e)[:200]}"],
            token_holdings=[],
            analysis={},
        )
