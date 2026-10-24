"""RMI backend entrypoint.

Delegates to app.factory.create_app. Keeps `uvicorn app.main:app` working
as the canonical launch path referenced by Makefile, AGENTS.md, and
CONTRIBUTING.md.

Phase 1 of AUDIT-2026-Q3.md, item P1.1.
"""

from __future__ import annotations

from app.factory import create_app
from app.routers.deep_scan import deep_scan_router
from app.routers.gmgn_intel import router as gmgn_intel_router
from app.routers.osint import osint_router

# Initialize distributed tracing (non-blocking — silently disabled if no OTLP endpoint)
try:
    from app.observability.tracing import init_tracing

    init_tracing(service_name="rmi-backend")
except Exception:  # noqa: S110 — optional dependency
    pass  # Tracing is non-critical

# Initialize GlitchTip / Sentry-compatible error tracking
try:
    from app.observability.error_tracking import init_error_tracking

    init_error_tracking()
except Exception:  # noqa: S110 — optional dependency
    pass  # Error tracking is non-critical

app = create_app()

# OSINT section router — guarded engine import inside the router module, so
# this registration never breaks app boot (503 until app.osint.engine lands).
app.include_router(osint_router, prefix="/api/osint", tags=["osint"])
app.include_router(deep_scan_router, prefix="/api/scan", tags=["scan"])

# GMGN intelligence layer — our normalization + our score (not GMGN rank order).
app.include_router(gmgn_intel_router, prefix="/api/v1/gmgn-intel", tags=["gmgn-intel"])

# Scalar docs UI (modern) at /scalar, alongside the default /docs Swagger UI.
# Optional dependency — guarded so the app boots even when scalar-fastapi
# is not present in the lockfile (mirrors the tracing/error_tracking guards above).
try:
    from scalar_fastapi import get_scalar_api_reference

    @app.get("/scalar", include_in_schema=False)
    async def scalar_docs():
        return get_scalar_api_reference(
            openapi_url=app.openapi_url,
            title=app.title,
        )
except ImportError:
    pass  # Scalar docs are non-critical
