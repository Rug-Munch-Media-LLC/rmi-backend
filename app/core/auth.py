"""RMI Backend - Auth middleware and API key verification.

Uses a WHITELIST approach: only explicitly listed paths are public.
Unknown paths (no matching route) pass through to the 404 handler which
returns proper RFC 7807 Problem Details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount, Route as StarletteRoute

from app.config import settings
from app.core.logging import get_logger
from app.core.problem_details import ProblemDetail

if TYPE_CHECKING:
    from fastapi import Request

log = get_logger(__name__)

RMI_AUTH_TOKEN = settings.rmi_auth_token

if not RMI_AUTH_TOKEN:
    env = settings.environment
    log.critical(
        "RMI_AUTH_TOKEN is not configured! "
        "Set RMI_AUTH_TOKEN in .env or via environment variable. "
        "Generate with: openssl rand -hex 32. "
        f"Environment: {env}"
    )

# Exact path matches (case-sensitive, any method)
PUBLIC_EXACT_PATHS = frozenset(
    {
        "/health",
        "/live",
        "/ready",
        "/metrics",
        "/version",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/",
        # Frontend dashboard data — cookie-auth browser calls these without
        # X-API-Key (see src/services/rmi/api.ts). Scan results are public
        # product data (token + risk) already shown on rugmunch.io/scanner.
        "/api/v1/data/scan-history",
        # Internal news service consumer — fetches recent scans with no
        # auth header (app/domains/news/news_service/fetchers_api.py).
        "/api/v1/scanner/recent",
    }
)

PUBLIC_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/public/",
    "/api/v1/databus/webhooks/",  # External webhook receivers (Helius, Moralis, etc.) — auth via Bearer token per-service
    "/api/v1/degenfeed/storage/",  # DegenFeed KV storage gateway — auth enforced in-router (X-API-Key OR Bearer, RMI_AUTH_TOKEN or RMI_INTERNAL_KEY)
    # KV migration endpoints (Phase 2 KV→selfhosted) — auth enforced in-router (same dual-token check)
    "/api/v1/rate-limits/",  # POST /report, GET /check
    "/api/v1/queue/",  # POST /enqueue, GET /dequeue
    "/api/v1/sessions",
    "/api/v1/chat-history",
    "/api/v1/identities",
    "/api/v1/bookmarks",
    "/ws/",
)


def is_public_path(path: str, method: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _path_has_route(path: str, routes: list) -> bool:
    """Check if a path matches any registered route. Simple prefix-based approach."""

    for route in routes:
        if isinstance(route, Mount):
            # Check if path starts with mount prefix
            if route.path != "" and (path == route.path or path.startswith(route.path.rstrip("/") + "/")):
                if route.routes:
                    # Check sub-routes
                    sub_path = path[len(route.path) :] if route.path != "/" else path
                    if _path_has_route(sub_path, route.routes):
                        return True
                # Mount with no sub-routes — assume it handles all paths under prefix
                if route.path != "/":
                    return True
        elif isinstance(route, StarletteRoute):
            # Simple path matching
            route_path = getattr(route, "path", "")
            if not route_path:
                continue
            # Convert Starlette route pattern to match against path
            # Strip path params like {param_id}
            import re

            pattern = re.sub(r"\{[^}]+\}", "[^/]+", route_path)
            pattern = "^" + re.escape(pattern).replace(r"\[^/\]\+", "[^/]+") + "$"
            if re.match(pattern, path):
                return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Any:
        path = request.url.path
        method = request.method

        # Allow public paths without any auth
        if is_public_path(path, method):
            return await call_next(request)

        # Check if this path actually exists as a route
        try:
            if not _path_has_route(path, self.app.routes):
                return await call_next(request)  # Let 404 handler return RFC 7807
        except Exception as exc:
            log.warning("route_check_failed path=%s err=%s", path, exc)
            # If route check fails, pass through — let normal auth handle it

        # Protected route — require authentication
        api_key = request.headers.get("X-API-Key", "")

        if not RMI_AUTH_TOKEN:
            env = settings.environment
            if env == "prod":
                return ProblemDetail.create(
                    status=503,
                    detail="Service misconfigured — RMI_AUTH_TOKEN not set",
                    code="auth.misconfigured",
                ).as_response()
            if settings.dev_bypass_auth and api_key:
                log.warning("DEV MODE: Auth bypassed via DEV_BYPASS_AUTH", path=path)
                return await call_next(request)
            log.warning("Auth denied (no RMI_AUTH_TOKEN)", path=path)
            return ProblemDetail.create(
                status=401,
                detail="Unauthorized — X-API-Key header required",
                code="auth.missing_token",
            ).as_response()

        if api_key != RMI_AUTH_TOKEN:
            log.warning("Auth denied: invalid X-API-Key", path=path)
            return ProblemDetail.create(
                status=401,
                detail="Unauthorized — invalid X-API-Key",
                code="auth.invalid_key",
            ).as_response()

        return await call_next(request)
