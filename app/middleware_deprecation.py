"""Deprecation + Sunset header middleware - extracted from middleware_setup for the structure gate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import Response

log = get_logger("middleware.deprecation")


def _try_register_deprecation_header(app: FastAPI) -> None:
    """Win #19 — inject Deprecation + Sunset headers on root-level routes.

    Known root-level routes that have /api/v1/* equivalents receive
    deprecation warning headers. Infra routes (health, metrics, MCP, docs)
    are excluded.
    """
    # Routes that should NOT receive deprecation headers
    _excluded_prefixes: tuple[str, ...] = (
        "/api/",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/live",
        "/ready",
        "/metrics",
        "/mcp/",
        "/.well-known/",
        "/llms.txt",
        "/tools",
        "/x402/docs",
        "/old-docs",
        "/favicon",
    )

    # Root-level route patterns that ARE deprecated (have /api/v1/* equivalents)
    _deprecated_prefixes: tuple[str, ...] = (
        "/scam-school/",
        "/stream/",
        "/rug_probability",
        "/history",
        "/narrative",
        "/reputation_score",
        "/webhook_register",
        "/webhook_list",
        "/investigation_report",
        "/cache/",
    )

    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        _sunset_date: str = "Thu, 15 Oct 2026 00:00:00 GMT"  # 90 days from 2026-07-17 (matches mount.py)

        class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                response = await call_next(request)
                path = request.url.path

                # Skip excluded paths
                if any(path.startswith(p) for p in _excluded_prefixes):
                    return response

                # Add deprecation headers to known root-level routes
                if any(path.startswith(p) for p in _deprecated_prefixes):
                    response.headers["Deprecation"] = "true"
                    response.headers["Sunset"] = _sunset_date
                    response.headers["Link"] = '<https://docs.rugmunch.io/api/versioning>; rel="deprecation"'

                return response

        app.add_middleware(DeprecationHeaderMiddleware)
        log.info("middleware_registered name=deprecation_header")
    except Exception as exc:
        log.warning("middleware_skipped name=deprecation_header err=%s", exc)
