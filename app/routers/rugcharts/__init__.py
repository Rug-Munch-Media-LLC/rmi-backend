"""RugCharts backend package.

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
