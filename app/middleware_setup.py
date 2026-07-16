"""RMI Backend - middleware registration.

Per v3 unfuck rule #7: add_middleware MUST be called at module level,
NOT inside lifespan. FastAPI rejects middleware added after startup.

This module owns every middleware registration. Adding a new middleware:
  1. Add a `try_register(app, "name")` block below
  2. Each block is isolated - one failure doesn't break the rest
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

log = get_logger(__name__)


def register_middleware(app: FastAPI) -> None:
    """Register all middleware. Each registration is isolated."""
    _try_register_cors(app)
    _try_register_security_headers(app)
    _try_register_prometheus(app)
    _try_register_cost_tracking(app)
    _try_register_tracing(app)


def _try_register_cors(app: FastAPI) -> None:
    """T19 - CORS hardening with strict allowlist."""
    try:
        from fastapi.middleware.cors import CORSMiddleware

        from app.config import settings

        # Validate CORS origins in production
        if settings.environment == "prod" and settings.cors_origins == ["*"]:
            raise RuntimeError(
                "CORS_ORIGINS cannot be ['*'] in production. "
                "Set CORS_ORIGINS to a specific list of allowed origins."
            )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Payment"],
        )
        log.info("middleware_registered name=cors")
    except Exception as exc:
        log.warning("middleware_skipped name=cors err=%s", exc)


def _try_register_security_headers(app: FastAPI) -> None:
    """T20 - Security headers on every response."""
    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        class SecurityHeadersMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                response = cast("Response", await call_next(request))
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["X-XSS-Protection"] = "1; mode=block"
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains; preload"
                )
                response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                # CSP applies to all paths including API docs
                if request.url.path in ["/docs", "/redoc", "/openapi.json"]:
                    response.headers["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                        "style-src 'self' 'unsafe-inline'; img-src 'self' data:"
                    )
                else:
                    response.headers["Content-Security-Policy"] = "default-src 'self'"
                return response

        app.add_middleware(SecurityHeadersMiddleware)
        log.info("middleware_registered name=security_headers")
    except Exception as exc:
        log.warning("middleware_skipped name=security_headers err=%s", exc)


def _try_register_prometheus(app: FastAPI) -> None:
    """Prometheus metrics middleware. Records every request."""
    try:
        from app.core.metrics import PrometheusMiddleware

        app.add_middleware(PrometheusMiddleware)
        log.info("middleware_registered name=prometheus")
    except Exception as exc:
        log.warning("middleware_skipped name=prometheus err=%s", exc)


def _try_register_cost_tracking(app: FastAPI) -> None:
    """M7 - per-tenant/per-route cost tracking."""
    try:
        from app.middleware.cost_tracking import CostBuffer, CostTrackingMiddleware

        app.add_middleware(CostTrackingMiddleware, buffer=CostBuffer())
        log.info("middleware_registered name=cost_tracking")
    except Exception as exc:
        log.warning("middleware_skipped name=cost_tracking err=%s", exc)


def _try_register_tracing(app: FastAPI) -> None:
    """Request-id + timing middleware (always on)."""
    try:
        from app.core.tracing import setup_tracing

        setup_tracing(app)
        log.info("middleware_registered name=tracing")
    except Exception as exc:
        log.warning("middleware_skipped name=tracing err=%s", exc)
