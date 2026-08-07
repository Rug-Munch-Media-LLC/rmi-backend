"""RMI Backend - Auth middleware and API key verification.

Uses a WHITELIST approach: only explicitly listed paths are public.
Unknown paths (no matching route) pass through to the 404 handler which
returns proper RFC 7807 Problem Details.

Auth tiers
==========
The middleware enforces a 3-tier authentication model:

Tier 0 — Public (no auth)
    Health, docs, product data reads, auth endpoints. Explicitly listed in
    PUBLIC_EXACT_PATHS / PUBLIC_PREFIXES and NOT overridden by
    PROTECTED_SUBPATHS. Examples: /health, /docs, /api/v1/market/overview,
    /api/v1/news/headlines.

Tier 1 — Shared secret
    Any of:
      - ``X-API-Key: $RMI_AUTH_TOKEN``
      - ``X-RMI-Key: $RMI_INTERNAL_KEY`` (falls back to $RMI_AUTH_TOKEN)
      - ``Authorization: Bearer <shared-secret>``
    Protects databus fetch, RAG write, bot endpoints, news admin/internal.
    A registered consumer (``X-Consumer-ID`` mapped in RMI_CONSUMER_KEYS)
    may use its own key.

Tier 2 — Per-user
    ``Authorization: Bearer <jwt>`` validated via get_current_user
    (app.auth.jwt_validation). Chat, user data, admin.

Consumer attribution
====================
Requests carrying the optional ``X-Consumer-ID`` header have it bound to
the structlog context (``consumer_id``), echoed back on the response, and
used to resolve per-consumer API keys. A warning is logged when a consumer
hits a protected endpoint without an ``X-Consumer-ID`` header.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount, Route as StarletteRoute

from app.config import settings
from app.core.logging import get_logger
from app.core.problem_details import ProblemDetail

if TYPE_CHECKING:
    from fastapi import Request

log = get_logger(__name__)

RMI_AUTH_TOKEN = settings.rmi_auth_token

# Internal service-to-service key (X-RMI-Key). Falls back to the auth token
# when RMI_INTERNAL_KEY is not configured so internal scripts keep working.
RMI_INTERNAL_KEY = settings.rmi_internal_key or RMI_AUTH_TOKEN

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
    # Public read-only data endpoints (CSRF still protects POST endpoints)
    "/api/v1/news/",  # News feed, headlines, articles, sentiment, sources — public product data
    "/api/v1/databus/news/",  # DataBus news endpoints (full_news, news_intel, market_brief, etc.)
    "/api/v1/databus/market/",  # DataBus market data (prices, trending, fear-greed)
    "/api/v2/scanner/",  # Scanner v2 — token/wallet scans are the core product
    # Public community & analytics pages (read-only; CSRF protects POST endpoints)
    "/api/v1/bulletin/",  # Bulletin board (public read: posts, badges, profiles)
    "/api/v1/bulletin-board/",  # Bulletin board v2 (Redis-backed, public read)
    "/api/v1/community-forensics/",  # Community forensics (public read: reports, leaderboard)
    "/api/v1/chat",  # AI chat — public (rate-limited per IP; auth optional via cookie)
    "/api/v1/market/",  # Market intel — public product data (overview, trending, prices)
    "/api/v1/stripe/",  # Stripe pricing & checkout — public-facing billing page
    "/api/v1/pricing",  # Pricing page data (frontend: PricingPage.tsx)
    "/api/v1/subscription",  # Subscription creation (frontend: PricingPage.tsx)
    "/api/v1/newsletter/",  # Newsletter — public signup & content (no API key needed)
    "/api/v1/token/",  # Token page — public token data (frontend: TokenPage.tsx, no API key needed)
    "/api/v1/ai/ai/",  # AI chat routes (ai/ai/*) — public (rate-limited per IP)
    "/api/v1/ai/stream",  # AI streaming responses — public (rate-limited per IP)
    "/api/v1/ai/chart-analysis",  # AI chart analysis — public (rate-limited per IP; CSRF-exempt via /api/v1/ai/ in middleware_setup.py)
    "/api/v1/scam-school/",  # Scam School — public education content (no API key needed)
    "/api/v1/rag/",  # RAG search & ask — public product feature (rate-limited); writes protected via PROTECTED_SUBPATHS
    "/api/v1/rugmaps/",  # RugMaps token graph & entity analysis — public product feature
    "/api/v1/rugcharts/",  # RugCharts trending, OHLCV, token intelligence — public product feature
    "/api/v1/bot/",  # Telegram bot API — public scan, ask, pulse endpoints (cross-platform connectivity); scan/ask protected via PROTECTED_SUBPATHS
    "/ws/",
)

# Specific sub-paths under public prefixes that must require auth.
# Value is the tier label (informational); enforcement is shared-secret +
# Bearer-JWT via the middleware. A path matches when it starts with a key.
PROTECTED_SUBPATHS: dict[str, str] = {
    "/api/v1/news/_admin/": "shared_secret",
    "/api/v1/news/internal/": "shared_secret",
    "/api/v1/rag/v2/ask": "shared_secret",
    "/api/v1/rag/v2/ingest": "shared_secret",
    "/api/v1/rag/v2/bulk-ingest": "shared_secret",
    "/api/v1/rag/v2/feedback": "shared_secret",
    "/api/v1/rag/v2/evaluate": "shared_secret",
    "/api/v1/bot/scan": "shared_secret",
    "/api/v1/bot/ask": "shared_secret",
}


def is_public_path(path: str, method: str) -> bool:
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def is_protected_subpath(path: str) -> bool:
    """True when a public-prefix path is explicitly overridden to require auth."""
    return any(path.startswith(prefix) for prefix in PROTECTED_SUBPATHS)


def _consumer_keys_for(request: Request) -> set[str]:
    """Per-consumer API keys registered for the request's X-Consumer-ID."""
    consumer_id = (request.headers.get("X-Consumer-ID") or "").strip()
    if not consumer_id:
        return set()
    consumer_keys = settings.rmi_consumer_keys or {}
    key = consumer_keys.get(consumer_id)
    if not key:
        return set()
    return {key}


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

        public = is_public_path(path, method)
        protected_subpath = is_protected_subpath(path)

        # Tier 0 — allow public paths without any auth (unless overridden).
        if public and not protected_subpath:
            response = await call_next(request)
            self._apply_consumer_attribution(request, response, require_attribution=False)
            return response

        # Check if this path actually exists as a route (skip for protected
        # subpaths — they must be authenticated regardless of route existence).
        if not protected_subpath:
            try:
                if not _path_has_route(path, self.app.routes):
                    return await call_next(request)  # Let 404 handler return RFC 7807
            except Exception as exc:
                log.warning("route_check_failed path=%s err=%s", path, exc)
                # If route check fails, pass through — let normal auth handle it

        # Protected route — require authentication (Tier 1 shared secret or
        # Tier 2 per-user JWT).
        auth_error = await self._authenticate(request)
        if auth_error is not None:
            return auth_error

        response = await call_next(request)
        self._apply_consumer_attribution(request, response, require_attribution=True)
        return response

    # ── Authentication ────────────────────────────────────────────────────

    async def _authenticate(self, request: Request) -> Any:
        """Validate Tier 1 (shared secret) then Tier 2 (per-user JWT).

        Returns a ProblemDetail response on failure, or None on success.
        """
        if not RMI_AUTH_TOKEN:
            env = settings.environment
            if env == "prod":
                return ProblemDetail.create(
                    status=503,
                    detail="Service misconfigured — RMI_AUTH_TOKEN not set",
                    code="auth.misconfigured",
                ).as_response()
            if settings.dev_bypass_auth and self._presented_credential(request):
                log.warning("DEV MODE: Auth bypassed via DEV_BYPASS_AUTH", path=request.url.path)
                return None
            log.warning("Auth denied (no RMI_AUTH_TOKEN)", path=request.url.path)
            return ProblemDetail.create(
                status=401,
                detail="Unauthorized — X-API-Key header required",
                code="auth.missing_token",
            ).as_response()

        # Tier 1 — shared secrets (X-API-Key, X-RMI-Key, Bearer <secret>)
        if self._has_valid_shared_secret(request):
            return None

        # Tier 2 — per-user JWT via Authorization: Bearer
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                from app.auth.jwt_validation import get_current_user

                user = await get_current_user(request)
            except Exception as exc:
                log.warning("jwt_validation_error path=%s err=%s", request.url.path, exc)
                user = None
            if user is not None:
                return None

        log.warning("Auth denied: invalid credentials", path=request.url.path)
        return ProblemDetail.create(
            status=401,
            detail="Unauthorized — invalid API key",
            code="auth.invalid_key",
        ).as_response()

    def _presented_credential(self, request: Request) -> str:
        """Return the first non-empty credential presented by the request."""
        api_key = (request.headers.get("X-API-Key") or "").strip()
        if api_key:
            return api_key
        rmi_key = (request.headers.get("X-RMI-Key") or "").strip()
        if rmi_key:
            return rmi_key
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return ""

    def _has_valid_shared_secret(self, request: Request) -> bool:
        """Accept X-API-Key == RMI_AUTH_TOKEN, X-RMI-Key == RMI_INTERNAL_KEY
        (fallback RMI_AUTH_TOKEN), or Bearer matching either — plus any key
        registered to the request's X-Consumer-ID."""
        api_key = (request.headers.get("X-API-Key") or "").strip()
        if api_key:
            valid = {RMI_AUTH_TOKEN} | _consumer_keys_for(request)
            if api_key in valid:
                return True

        rmi_key = (request.headers.get("X-RMI-Key") or "").strip()
        if rmi_key:
            valid = {RMI_INTERNAL_KEY, RMI_AUTH_TOKEN} | _consumer_keys_for(request)
            if rmi_key in valid:
                return True

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            valid = {RMI_AUTH_TOKEN, RMI_INTERNAL_KEY} | _consumer_keys_for(request)
            if token in valid:
                return True

        return False

    # ── Consumer attribution ──────────────────────────────────────────────

    def _apply_consumer_attribution(self, request: Request, response: Any, *, require_attribution: bool) -> None:
        """Bind optional X-Consumer-ID to structlog context + echo it back.

        Logs a warning when a consumer hits a protected endpoint without
        identifying itself via X-Consumer-ID.
        """
        consumer_id = (request.headers.get("X-Consumer-ID") or "").strip()
        if consumer_id:
            structlog.contextvars.bind_contextvars(consumer_id=consumer_id)
            response.headers["X-Consumer-ID"] = consumer_id
        elif require_attribution:
            log.warning(
                "consumer_attribution_missing path=%s method=%s",
                request.url.path,
                request.method,
            )
