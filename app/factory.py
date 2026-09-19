"""RMI Backend - FastAPI app factory.

The single source of truth for app composition. Every concern
(lifespan, middleware, error handlers, routers) lives in its own
module and is wired together here.

Composition order (matters):
  1. Create FastAPI instance with metadata
  2. Register error handlers (before middleware so they can format responses)
  3. Register middleware (must be at module level, not lifespan)
  4. Mount routers (after middleware so routes can use it)
  5. Lifespan runs on startup/shutdown

Per v4.0 §T01 + ADR-0001 (strangler fig), this factory:
  - Replaces the 8475-line legacy monolith
  - Mounts new v1 routers as the ONLY backend surface
  - Each mount is isolated (one failure does not break others)
  - Frozen file: no mass regex allowed; targeted edits only
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Public constants ────────────────────────────────────────────────
VERSION: Final = "2026.06.21"
TITLE: Final = "RMI Backend"
DESCRIPTION: Final = (
    "Rug Munch Intelligence - institutional-grade crypto intelligence API. "
    "13+ chains, 96 data providers, 8 MCP tools, x402 paid tier, sovereign-first FOSS."
)


def create_app() -> FastAPI:
    """Build the FastAPI app. Single source of truth for composition.

    Every concern delegates to its own module:
      - app.lifespan.lifespan            startup/shutdown
      - app.middleware_setup.register    middleware
      - app.error_handlers.register      exception handlers
      - app.mount.mount_all              every router

    Note: load_dotenv is called here (not in lifespan) so env vars are
    available before any app module is imported (e.g. db_client,
    intel_pipeline read env at module level).
    """
    from dotenv import load_dotenv

    load_dotenv("/app/.env", override=True)

    from app.error_handlers import register_error_handlers
    from app.lifespan import lifespan
    from app.middleware_setup import register_middleware
    from app.mount import mount_all

    log.info("rmi_backend_creating v=%s", VERSION)

    app = FastAPI(
        title=TITLE,
        version=VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    # Order matters: handlers before middleware before routes
    register_error_handlers(app)
    register_middleware(app)

    # Canonical health probes (/live liveness, /ready readiness, /health deep
    # diagnostic) — registered BEFORE mount_all so orchestrator probes work
    # even if a router mount fails. health_route is intentionally excluded
    # from mount.ROUTER_MODULES to avoid double-registration.
    from app.core.health_route import router as health_router

    app.include_router(health_router)

    mounted = mount_all(app)

    log.info(
        "rmi_backend_ready mounted=%d total_routes=%d",
        mounted,
        len(app.routes),
    )
    return app
