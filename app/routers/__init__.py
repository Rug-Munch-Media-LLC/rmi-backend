"""API route definitions — organized by feature domain.

This package contains all FastAPI APIRouter definitions.
Each module registers routes via `router = APIRouter(...)`.

Sub-packages:
  - v1/           Legacy v1 API routes
  - admin/        Admin panel routes
  - market_intel/ Market intelligence routes
  - mcp_server/   MCP server protocol routes
  - x402/         x402 payment-gated routes
  - x402_databus_tools/    x402 databus tool routes
  - x402_advanced_tools/   x402 advanced analysis tool routes
  - x402_enrichment/       x402 enrichment routes
  - x402_middleware/       x402 middleware routes
  - scam_school/  Scam education content routes
"""

from __future__ import annotations

__all__: list[str] = []
