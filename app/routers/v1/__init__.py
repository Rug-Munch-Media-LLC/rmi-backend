"""V1 API router aggregator — /api/v1/*.

The strangler fig: new v1 routes live here and co-exist with legacy
root-level routes. Legacy routes get deprecation redirects in their
original router files. This module aggregates all v1 sub-routers into
a single mount point.

Per v4.0 §T01 + ADR-0001:
  - All new routes go through /api/v1/
  - Legacy root-level routes receive 301 redirects + deprecation headers
  - DO NOT delete old routes — sunset after 90-day deprecation window
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Aggregator ────────────────────────────────────────────────────────
v1_router = APIRouter(prefix="/api/v1", tags=["v1"])

# ── Migrated domains ──────────────────────────────────────────────────
# Each domain's v1 router is imported and mounted here. The original
# routers remain mounted at root level with deprecation redirects.

# Health endpoints (mirror root-level /health, /live, /ready)
from app.routers.v1.health import router as health_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(health_v1_router)

# Version info
from app.routers.v1.version import router as version_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(version_v1_router)

# Analyze endpoints
from app.routers.v1.analyze import router as analyze_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(analyze_v1_router)

# Token endpoints
from app.routers.v1.tokens import router as tokens_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(tokens_v1_router)

# Deployer endpoints
from app.routers.v1.deployer import router as deployer_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(deployer_v1_router)

# Contract endpoints
from app.routers.v1.contract import router as contract_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(contract_v1_router)

# Entity resolution endpoints
from app.routers.v1.entities import router as entities_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(entities_v1_router)

# Cross-chain endpoints
from app.routers.v1.cross_chain import (  # noqa: E402 — module docstring precedes import block
    router as cross_chain_v1_router,
)

v1_router.include_router(cross_chain_v1_router)

# Provider status endpoints (Win #20)
from app.routers.v1.providers import (  # noqa: E402 — module docstring precedes import block
    router as providers_v1_router,
)

v1_router.include_router(providers_v1_router)

# Entity cross-chain endpoint (Win #20)
from app.routers.v1.entity import router as entity_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(entity_v1_router)

# Custom report generation endpoint (Win #20)
from app.routers.v1.reports import router as reports_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(reports_v1_router)

# Bulk alert configuration endpoint (Win #20)
from app.routers.v1.alerts import router as alerts_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(alerts_v1_router)

# Full routers re-exported at v1 prefix (same handlers, new path)
# Scam School: root-level routes at /scam-school/* become /api/v1/scam-school/*
from app.routers.scam_school import router as scam_school_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(scam_school_router)

# x402 Advanced Tools: routes at /stream/alerts etc. become /api/v1/x402/stream/alerts
from app.routers.x402_advanced_tools import (  # noqa: E402 — module docstring precedes import block
    router as x402_advanced_router,
)

v1_router.include_router(x402_advanced_router, prefix="/x402")

# x402 Premium Tools: routes at /reputation_score etc. become /api/v1/x402/reputation_score
from app.routers.x402_premium_tools import (  # noqa: E402 — module docstring precedes import block
    router as x402_premium_router,
)

v1_router.include_router(x402_premium_router, prefix="/x402")

# API index at /api (served from /api/v1 by FastAPI routing but we add it here
# so it's available at both /api and /api/v1)
from app.routers.v1.api_index import router as api_index_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(api_index_router)

# Frontend data endpoints — GET /api/v1/data/scan-history
from app.routers.v1.data import router as data_v1_router  # noqa: E402 — module docstring precedes import block

v1_router.include_router(data_v1_router)

# Scanner recent — GET /api/v1/scanner/recent (news service consumer)
from app.routers.v1.scanner import (  # noqa: E402 — module docstring precedes import block
    router as scanner_recent_v1_router,
)

v1_router.include_router(scanner_recent_v1_router)

log.info("v1_router_initialized domains=%d", len(v1_router.routes))

# Export as `router` for mount.py auto-discovery
router = v1_router
