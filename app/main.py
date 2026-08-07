"""RMI backend entrypoint.

Delegates to app.factory.create_app. Keeps `uvicorn app.main:app` working
as the canonical launch path referenced by Makefile, AGENTS.md, and
CONTRIBUTING.md.

Phase 1 of AUDIT-2026-Q3.md, item P1.1.
"""

from __future__ import annotations

from app.factory import create_app

# Initialize distributed tracing (non-blocking — silently disabled if no OTLP endpoint)
try:
    from app.observability.tracing import init_tracing

    init_tracing(service_name="rmi-backend")
except Exception:
    pass  # Tracing is non-critical

# Initialize GlitchTip / Sentry-compatible error tracking
try:
    from app.observability.error_tracking import init_error_tracking

    init_error_tracking()
except Exception:
    pass  # Error tracking is non-critical

app = create_app()
