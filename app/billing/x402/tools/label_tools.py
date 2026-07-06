"""x402 address-labels tools — multi-source wallet label resolver.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/address_labels

Resolved sources:
  Etherscan labels, RolodETH, walletLabels.xyz, bluepages.fyi, internal RAG.

Other SENTINEL modules live in tools/evidence_tools.py and tools/report_tools.py.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.billing.x402.shared import (
    SentinelModuleRequest,
    record_x402_payment,
)

sub_router = APIRouter(tags=["x402-labels"])


@sub_router.post("/address_labels")
async def address_labels_endpoint(req: SentinelModuleRequest):
    """SENTINEL Address Labels - multi-source wallet address labeling.

    Pricing: $0.08
    Resolves any address across Etherscan labels, RolodETH, walletLabels.xyz,
    bluepages.fyi, and internal RAG. Returns unified labels (exchange, MEV bot,
    known scammer, deployer, etc.).
    """
    try:
        from app.scanners.sentinel_pipeline import run_address_labels

        result = await run_address_labels(req.address, req.chain)
        await record_x402_payment("address_labels", "0.08", req.address)

        return {
            "tool": "SENTINEL Address Labels",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
