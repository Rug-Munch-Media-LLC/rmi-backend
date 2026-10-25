"""One quota middleware for every public route.

The bucket is the fleet's canonical ``UnifiedRateLimiter`` (Redis with an
in-memory fallback), so the public surface shares one limiter with the rest of
the API and a Redis outage cannot silently remove the limit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.rate_limiter import get_rate_limiter
from app.domains.public_api.tiers import PublicTier, resolve_tier

if TYPE_CHECKING:
    from starlette.requests import Request

PUBLIC_PREFIX = "/api/v1/public/"
_PLAN_HEADER = "x-plan"
_KEY_HEADER = "x-api-key"
PROBLEM_MEDIA_TYPE = "application/problem+json"


def _caller(request: Request) -> tuple[str, bool]:
    key = request.headers.get(_KEY_HEADER, "").strip()
    return (key or request.client.host if request.client else "unknown"), bool(key)


def _rate_headers(tier: PublicTier, headers: dict[str, str]) -> dict[str, str]:
    stamped = dict(headers)
    stamped["X-RateLimit-Tier"] = tier.name
    stamped["X-RateLimit-Policy"] = f"{tier.requests_per_min};w=60"
    return stamped


def _too_many(tier: PublicTier, headers: dict[str, str], path: str) -> Response:
    body = {
        "type": "about:blank",
        "title": "Too Many Requests",
        "status": 429,
        "detail": f"{tier.name} tier allows {tier.requests_per_min} requests/minute",
        "instance": path,
        "tier": tier.name,
    }
    return Response(
        content=json.dumps(body),
        status_code=429,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=_rate_headers(tier, headers),
    )


class PublicQuotaMiddleware(BaseHTTPMiddleware):
    """Enforce the public tier table on ``/api/v1/public/*`` and nothing else."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(PUBLIC_PREFIX):
            return await call_next(request)

        identity, authenticated = _caller(request)
        tier = resolve_tier(request.headers.get(_PLAN_HEADER), authenticated=authenticated)
        limiter = get_rate_limiter()
        allowed, headers = await limiter.acquire(
            key=f"public:{tier.name}:{identity}",
            rate=tier.requests_per_min / 60.0,
            burst=float(tier.requests_per_min),
        )
        if not allowed:
            return _too_many(tier, headers, path)

        request.state.public_tier = tier
        response = await call_next(request)
        for name, value in _rate_headers(tier, headers).items():
            response.headers[name] = value
        return response
