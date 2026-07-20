"""RMI Backend - Auth middleware and API key verification.

Uses a WHITELIST approach: only explicitly listed paths are public.
Everything else — including all /api/v1/* routes not in the whitelist —
requires authentication.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Load auth token from Settings (which reads from .env / env vars).
RMI_AUTH_TOKEN = settings.rmi_auth_token

if not RMI_AUTH_TOKEN:
    env = settings.environment
    log.critical(
        "RMI_AUTH_TOKEN is not configured! "
        "Set RMI_AUTH_TOKEN in .env or via environment variable. "
        "Generate with: openssl rand -hex 32. "
        f"Environment: {env}"
    )

# ── Whitelist of truly public paths (no auth required) ─────────────────
# Only these exact paths and prefixes bypass auth. Everything else —
# including all /api/v1/* routes not listed here — requires authentication.

# Exact path matches (case-sensitive, any method)
PUBLIC_EXACT_PATHS = frozenset(
    {
        "/health",
        "/live",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/",
    }
)

# Path prefixes that are always public (any method, any path under prefix)
PUBLIC_PREFIXES = (
    "/api/v1/auth/",
    "/api/v1/public/",
    "/ws/",
)


def is_public_path(path: str, method: str) -> bool:
    """Check if a path is publicly accessible without auth (whitelist).

    Only paths in PUBLIC_EXACT_PATHS or matching a PUBLIC_PREFIXES entry
    are considered public. All other routes — including /api/v1/token/*,
    /api/v1/databus/*, /api/v1/scanner/*, /api/v1/wallet/*, etc. —
    require authentication.
    """
    if path in PUBLIC_EXACT_PATHS:
        return True

    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Allow public paths without any auth
        if is_public_path(path, method):
            return await call_next(request)

        # Protected route — require authentication
        api_key = request.headers.get("X-API-Key", "")

        if not RMI_AUTH_TOKEN:
            # No token configured at all — this is a misconfiguration
            env = settings.environment
            if env == "prod":
                # Production: reject with 503 — service is misconfigured
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Service misconfigured — RMI_AUTH_TOKEN not set",
                    },
                )
            # Dev mode: only bypass if DEV_BYPASS_AUTH is explicitly set
            if settings.dev_bypass_auth and api_key:
                log.warning(
                    "DEV MODE: Authentication bypassed via DEV_BYPASS_AUTH",
                    path=path,
                )
                return await call_next(request)
            # All other cases: reject
            log.warning("Auth denied (no RMI_AUTH_TOKEN configured)", path=path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized — X-API-Key header required"},
            )

        # Token configured — must match exactly
        if api_key != RMI_AUTH_TOKEN:
            log.warning("Auth denied: invalid X-API-Key", path=path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized — invalid X-API-Key"},
            )

        return await call_next(request)
