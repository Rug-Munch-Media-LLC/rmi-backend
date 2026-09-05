"""RugCharts REST sub-routers.

Split of the former monolithic ``routes.py`` (894 lines) into focused
sub-modules, each owning its own ``APIRouter`` with the canonical
``/api/v1/rugcharts`` prefix:

- ``trending``  — trending tokens, health probe, analyze/ta snapshots, plugins
- ``ohlcv``     — OHLCV candles + AI-assisted TA overlay
- ``intel``     — token intelligence, whales, volume analysis/breakdown
- ``signals``   — trading signals from labeled whale-trade data

Honesty contract (2026-08-24 audit WIN-1) carries over unchanged:
every candle/intel response reports its ACTUAL data ``source``
(clickhouse | geckoterminal | none); unknown metrics are ``null``,
never fabricated zeros.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import intel, ohlcv, signals, trending

router = APIRouter(prefix="/api/v1/rugcharts", tags=["rugcharts"])
router.include_router(trending.router)
router.include_router(ohlcv.router)
router.include_router(intel.router)
router.include_router(signals.router)

__all__ = ["intel", "ohlcv", "router", "signals", "trending"]
