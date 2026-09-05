"""
RugMaps API Router — CANONICAL LIVE API for /api/v1/rugmaps/*.

Canonical home (2026-08-30 clean-cut decision):
  THIS router is the single live production API for RugMaps. The standalone
  repo rugmaps/ is the PRODUCT HOME (frontend + standalone self-hosted
  packaging); its thin backend serves standalone deployments only and must
  NOT gain features that belong in this live API. One path per surface:
  /api/v1/rugmaps — no duplicate prefixes anywhere.

RMI's interactive wallet visualization.
Self-contained engine, no RugMaps - no external API dependency.
Connects to /api/v1/rugmaps/*

Split into sub-routers (2026-09-05):
  - tokens.py    — token map data routes
  - clusters.py  — cluster/holder analysis routes
  - flows.py     — fund flow / transfer routes
  - _helpers.py  — shared helpers
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.rugmaps_router._helpers import RugMapsRequest
from app.routers.rugmaps_router.clusters import router as clusters_router
from app.routers.rugmaps_router.flows import router as flows_router
from app.routers.rugmaps_router.tokens import router as tokens_router

router = APIRouter(prefix="/api/v1/rugmaps", tags=["rugmaps"])
router.include_router(tokens_router)
router.include_router(clusters_router)
router.include_router(flows_router)

__all__ = ["RugMapsRequest", "router"]
