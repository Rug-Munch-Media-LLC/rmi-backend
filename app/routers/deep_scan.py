"""Deep-scan REST API router — exposes the deep-scan orchestrator.

Routes:
    GET  /api/scan/deep/{address}  → deep_scan_address(address)
    POST /api/scan/deep            → deep_scan_address(address, chain=..., transfers=...)

The orchestrator module (``app.deep_scan``) is loaded lazily via a guarded
import. If it is missing, these endpoints return 503 "deep scan not available"
instead of breaking app boot.

Composition contract (see :mod:`app.deep_scan`):
    * ``deep_scan_address(address, *, chain="auto", transfers=None, ...)``
      -> ``DeepScanReport`` with ``.to_dict()``.
    * Deep scans are heavier than the per-provider endpoints (ML modules +
      RPC bytecode fetch + decompile + EVM sell simulation), so the rate
      limit is conservative.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency

logger = get_logger(__name__)

# Prefix is applied at include time (app/main.py) — mirrors router convention
# where mount.py passes no prefix because routers own theirs. We own the prefix
# in the single registration line to keep the change isolated.
deep_scan_router = APIRouter(tags=["scan"])

# ── Guarded orchestrator import ─────────────────────────────────────────────
_scan: Any = None
try:
    _scan = importlib.import_module("app.deep_scan")
    logger.info("deep_scan_loaded module=%s", getattr(_scan, "__name__", "app.deep_scan"))
except ImportError as exc:
    # Orchestrator not available yet — endpoints 503 until it lands.
    logger.debug("deep_scan_unavailable err=%s", exc)


class DeepScanRequest(BaseModel):
    """POST /api/scan/deep body."""

    address: str = Field(..., min_length=1, description="Address to scan (0x EVM or base58 Solana)")
    chain: str | None = Field(None, description='Chain name or "auto" (default: auto-resolve)')
    transfers: list[dict[str, Any]] | None = Field(
        None,
        description="Optional transfer edges ({from_addr/to_addr or source/target}) enabling PyGOD graph scoring",
    )


def _orchestrator_or_503() -> Any:
    """Return the loaded orchestrator module or raise 503 if it is unavailable."""
    if _scan is None:
        raise HTTPException(status_code=503, detail="deep scan not available")
    return _scan


def _map_scan_errors(exc: Exception) -> HTTPException:
    """Map orchestrator exceptions to HTTP errors with clear messages."""
    if isinstance(exc, (ValueError, TypeError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=f"deep scan failed: {exc}")


def _to_report_dict(report: Any) -> dict[str, Any]:
    """Normalize a DeepScanReport (or dict) into a JSON-safe dict.

    Uses ``default=str`` safe serialization so findings with non-JSON-native
    values (datetime, enums, bytes, numpy scalars, ...) never break the
    response.
    """
    if hasattr(report, "to_dict"):
        report = report.to_dict()
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="deep scan returned an unexpected result")
    return json.loads(json.dumps(report, default=str))


# ── Endpoints ────────────────────────────────────────────────────────────────


@deep_scan_router.get("/deep/{address}")
def get_deep_scan(
    address: str,
    _rl: None = Depends(get_rate_limit_dependency("deep_scan", rate=0.1, burst=2)),
) -> dict[str, Any]:
    """Run the full deep-scan orchestrator over one address (chain auto-resolved)."""
    try:
        report = _orchestrator_or_503().deep_scan_address(address)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_scan_errors(exc) from exc
    return _to_report_dict(report)


@deep_scan_router.post("/deep")
def post_deep_scan(
    request: DeepScanRequest,
    _rl: None = Depends(get_rate_limit_dependency("deep_scan", rate=0.1, burst=2)),
) -> dict[str, Any]:
    """Run the full deep-scan orchestrator with optional chain + transfer graph."""
    kwargs: dict[str, Any] = {}
    if request.chain:
        kwargs["chain"] = request.chain
    if request.transfers:
        kwargs["transfers"] = request.transfers
    try:
        report = _orchestrator_or_503().deep_scan_address(request.address, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise _map_scan_errors(exc) from exc
    return _to_report_dict(report)
