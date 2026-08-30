"""RugCharts backend package — CANONICAL LIVE API.

Canonical home (2026-08-30 clean-cut decision):
  THIS package is the single live production API for /api/v1/rugcharts/*
  (REST) and /ws/trades (live tape). The standalone repo
  rugcharts/ is the PRODUCT HOME (frontend + standalone
  self-hosted packaging); its thin backend serves standalone deployments
  only and must NOT gain features that belong in this live API. One path
  per surface: /api/v1/rugcharts — no duplicate prefixes anywhere.

Self-contained charting intelligence surface:
routes (REST), ws (live trade stream), helpers (data fetchers),
ta (technical indicators), classifier (wallet behavior labels).

Extraction note: this package intentionally depends only on external
services (ClickHouse chain-indexer, Redis, GeckoTerminal/DexScreener HTTP)
plus thin app-core utilities, so it can be lifted into a standalone
rugcharts service without rewiring.
"""

from .routes import router

__all__ = ["router"]
