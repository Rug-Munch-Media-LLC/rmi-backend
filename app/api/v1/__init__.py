"""V1 API router aggregator.

The strangle: new v1 routes are added here as domains migrate. The legacy
main.py still mounts all old routes; we ADD new v1 routes on top so they
co-exist until cutover.

To add a new domain:
    1. Create app/api/v1/<group>/<domain>.py with APIRouter
    2. Import and append it to `api_v1_router` below
    3. Mount the route prefix in the domain's __init__.py
"""

from __future__ import annotations

from fastapi import APIRouter

# Aggregator list - populated as domains migrate.
# Each entry is an APIRouter from app/api/v1/<group>/<domain>.py.
api_v1_router: list[APIRouter] = []

# Aggregator router - single mount point for v1.
# When domains migrate, replace this with a real aggregator:
#     from app.api.v1.public import router as public_router
#     api_v1_router.append(public_router)
router = APIRouter(prefix="/api/v1", tags=["v1"])

# ── Migrated domains ───────────────────────────────────────────────────
# Each migrated domain is imported here. The router exposes endpoints
# at /api/v1/<domain>/* (path defined per-router).
#
# During strangelfig, the LEGACY /api/v1/<domain>/* endpoints remain
# mounted in main.py. The new v1 router is mounted at the same path
# (FastAPI handles prefix-based routing) - first match wins, so the
# legacy stays until we explicitly remove it.

from app.api.v1.auth.alerts import router as alerts_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(alerts_router)

from app.api.v1.public.wallet import router as wallet_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(wallet_router)

from app.api.v1.public.token import router as token_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(token_router)

from app.api.v1.public.scanner import router as scanner_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(scanner_router)

# x402 moved to app.domains.x402 (T34 v2)
# Old app/api/v1/x402/payments.py removed to avoid model conflicts

from app.api.v1.rag.search import router as rag_v2_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(rag_v2_router)

from app.api.v1.admin.alerts_webhook import (  # noqa: E402 — module docstring precedes import block
    router as admin_alerts_webhook_router,
)

api_v1_router.append(admin_alerts_webhook_router)

from app.api.v1.catalog import router as catalog_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(catalog_router)

from app.api.v1.agent.analyze import router as agent_router  # noqa: E402 — module docstring precedes import block

api_v1_router.append(agent_router)

from app.api.v1.ai.chart_analysis import (  # noqa: E402 — module docstring precedes import block
    router as ai_chart_router,
)

api_v1_router.append(ai_chart_router)


def build_v1_router() -> APIRouter:
    """Construct the v1 aggregator with all migrated routes mounted."""
    aggregated = APIRouter(prefix="/api/v1")
    for r in api_v1_router:
        aggregated.include_router(r)
    return aggregated
