"""x402 paid-tools package.

Phase 3A of AUDIT-2026-Q3.md.

Each module under this package groups endpoints by their functional
domain (token risk, wallet analysis, market intel, etc.). Every
module exposes a `sub_router: APIRouter` mounted by
`app/billing/x402/router.py`.
"""
from __future__ import annotations
