"""x402 main router — aggregates all sub-tool routers.

Phase 3A of AUDIT-2026-Q3.md: split the 5,780-LOC x402_tools.py god-file.

The canonical mount point for the x402 paid-tool surface. Each
sub-tool module under app/billing/x402/tools/ owns an APIRouter
(`sub_router`) with no prefix of its own; this module applies the
shared ``/api/v1/x402-tools`` prefix when mounting every one into the
top-level router that callers include via ``app.include_router(router)``.

Legacy ``app/routers/x402_tools.py`` is preserved as a thin re-export
shim that re-exports this router; downstream code keeps working
unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.domains.billing.x402.tools.analysis_tools import sub_router as analysis_tools
from app.domains.billing.x402.tools.deployer_tools import sub_router as deployer_tools
from app.domains.billing.x402.tools.evidence_tools import sub_router as evidence_tools
from app.domains.billing.x402.tools.integration import sub_router as integration_tools
from app.domains.billing.x402.tools.label_tools import sub_router as label_tools
from app.domains.billing.x402.tools.market_tools import sub_router as market_tools
from app.domains.billing.x402.tools.report_tools import sub_router as report_tools
from app.domains.billing.x402.tools.token_tools import sub_router as token_tools
from app.domains.billing.x402.tools.wallet_tools import sub_router as wallet_tools

# The original god-file used prefix="/api/v1/x402-tools" on its single
# APIRouter.  After the P3A split, every sub-tool module exposes a
# prefix-less APIRouter; we apply the shared prefix here exactly once.
_X402_TOOLS_PREFIX = "/api/v1/x402-tools"

router = APIRouter(prefix="", tags=["x402-facade"])
router.include_router(token_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(wallet_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(deployer_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(market_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(analysis_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(evidence_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(report_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(label_tools, prefix=_X402_TOOLS_PREFIX)
router.include_router(integration_tools, prefix=_X402_TOOLS_PREFIX)


__all__ = ["router"]
