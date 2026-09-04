"""RMI Backend - Auth middleware and API key verification.

Uses a WHITELIST approach: only explicitly listed paths are public.
Unknown paths (no matching route) pass through to the 404 handler which
returns proper RFC 7807 Problem Details.

Auth tiers
==========
The middleware enforces a 3-tier authentication model:

Tier 0 — Public (no auth required, but presented credentials ARE validated)
    Health, docs, product data reads, auth endpoints. Explicitly listed in
    PUBLIC_EXACT_PATHS / PUBLIC_PREFIXES and NOT overridden by
    PROTECTED_SUBPATHS. Examples: /health, /docs, /api/v1/market/overview,
    /api/v1/news/headlines.

    Anonymous requests are allowed (the route is public). However, if a
    request DOES present a credential (X-API-Key, X-RMI-Key, Bearer, or the
    rmi_token cookie), that credential is validated instead of being skipped:
      - valid shared secret / JWT  -> allowed, user info attached to
                                      request.state (auth_method, user)
      - invalid / revoked token    -> 401 (uniform rejection closes the
                                      route-existence timing side channel)

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

import hashlib
import json
import secrets
import time
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
    # x402 paid-tool surface — payment-gated (X-Payment header, not API key).
    # The payment gate (app.domains.x402.middleware.payment_gate) enforces
    # payment per-route: no X-Payment -> 402 invoice challenge. Auth here would
    # 401 before the invoice can be issued, breaking the x402 pay-then-call flow.
    "/api/v1/x402-tools/",
    "/api/v1/databus/webhooks/",  # External webhook receivers (Helius, Moralis, etc.) — auth via Bearer token per-service
    "/api/v1/degenfeed/storage/",  # DegenFeed KV storage gateway — auth enforced in-router (X-API-Key OR Bearer, RMI_AUTH_TOKEN or RMI_INTERNAL_KEY)
    # KV migration endpoints (Phase 2 KV→selfhosted) — auth enforced in-router (same dual-token check)
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
    "/api/v1/admin/backend/",  # Admin dashboard — session auth via X-Admin-Session header (own auth layer, not API key)
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


def _constant_time_in(candidate: str, valid: set[str]) -> bool:
    """Constant-time membership test: True if candidate equals any valid key.

    Uses secrets.compare_digest (timing-safe) instead of ``in`` set membership,
    which short-circuits on prefix/length and leaks key equality over the wire.
    """
    if not candidate:
        return False
    candidate_b = candidate.encode("utf-8")
    return any(key and secrets.compare_digest(candidate_b, key.encode("utf-8")) for key in valid)


# ── Redis-backed admin API keys (rmi:api_keys) ──────────────────────────────
# The admin panel (app/routers/admin/backend.py) creates and revokes API keys
# in the Redis hash ``rmi:api_keys``. The auth middleware must consult that
# store so a revoked key is actually rejected — otherwise revocation is a
# no-op and a "revoked" key keeps authenticating. Keys are stored as a sha256
# hash of the raw key (the raw value is only shown once at creation), so we
# hash the presented key and match against stored key_hash values.
#
# Read-through cache: the store is small (admin-created keys) and this runs on
# every request, so we cache the set of valid (active, unexpired) key hashes
# for a short TTL. A revoked key takes effect within the TTL (5 min), matching
# the existing degenfeed_storage.py pattern.

_ADMIN_API_KEYS_CACHE_TTL = 300  # 5 min — short enough to revoke quickly
_admin_api_keys_cache: tuple[float, set[str]] | None = None


def _invalidate_admin_api_keys_cache() -> None:
    """Drop the cached valid-key set so the next request re-reads Redis."""
    global _admin_api_keys_cache
    _admin_api_keys_cache = None


async def _valid_admin_api_key_hashes() -> set[str]:
    """Return the set of sha256 hashes of currently-valid (active, unexpired)
    admin API keys from the Redis ``rmi:api_keys`` store.

    Uses a short-TTL read-through cache. Returns an empty set when Redis is
    unavailable or the store is empty — callers fall back to env-var secrets.
    """
    global _admin_api_keys_cache
    now = time.monotonic()
    if _admin_api_keys_cache is not None:
        cached_at, hashes = _admin_api_keys_cache
        if now - cached_at < _ADMIN_API_KEYS_CACHE_TTL:
            return hashes

    hashes: set[str] = set()
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is not None:
            raw = await r.hgetall("rmi:api_keys")
            for data in raw.values():
                try:
                    d = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if d.get("status") != "active":
                    continue
                # Expiry check — expires_at is a unix timestamp.
                expires_at = d.get("expires_at")
                if expires_at and isinstance(expires_at, (int, float)) and expires_at < time.time():
                    continue
                key_hash = d.get("key_hash")
                if key_hash:
                    hashes.add(key_hash)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("admin_api_keys_lookup_failed err=%s", exc)

    _admin_api_keys_cache = (now, hashes)
    return hashes


async def _is_valid_admin_api_key(api_key: str) -> bool:
    """True when the presented key matches an active (non-revoked, unexpired)
    admin API key in the Redis ``rmi:api_keys`` store.

    This is the enforcement that makes admin-panel revocation actually take
    effect: a key whose status was flipped to ``revoked`` is no longer in the
    valid set and is rejected.
    """
    if not api_key:
        return False
    candidate_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    valid = await _valid_admin_api_key_hashes()
    # The values compared are sha256 hashes (one-way), not raw secrets, so
    # plain set membership is safe — an attacker cannot recover the key from
    # the hash, and the hash is not transmitted.
    return candidate_hash in valid


def _path_has_route(path: str, routes: list[Any]) -> bool:
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

        # Tier 0 — public paths. Accept unauthenticated requests, but
        # validate any presented credential instead of skipping the auth
        # chain entirely. This closes the PUBLIC_PREFIXES bypass: invalid
        # or revoked tokens are no longer silently accepted, valid tokens
        # have their user info attached to request.state, and credentialed
        # route probes now get a uniform 401 instead of pass-through.
        if public and not protected_subpath:
            auth_error = await self._authenticate_optional(request)
            if auth_error is not None:
                return auth_error
            response = await call_next(request)
            self._apply_consumer_attribution(request, response, require_attribution=False)
            return response

        # Check if this path actually exists as a route (skip for protected
        # subpaths — they must be authenticated regardless of route existence).
        if not protected_subpath:
            try:
                app_routes = getattr(self.app, "routes", [])
                if not _path_has_route(path, app_routes):
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

        # Tier 1b — Redis-backed admin API keys (rmi:api_keys). A key created
        # via the admin panel that is still active (not revoked, unexpired).
        if await self._has_valid_admin_api_key(request):
            request.state.auth_method = "shared_secret"
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

    async def _authenticate_optional(self, request: Request) -> Any:
        """Validate a presented credential on a public route without requiring one.

        Public routes accept anonymous requests (no credential -> None).
        When a credential IS presented it must be valid:
          - a valid shared secret is accepted (auth_method attached)
          - a valid JWT is accepted (user claims attached to request.state.user)
          - an invalid/revoked token is rejected with 401 — it must NOT
            silently pass through (this was the PUBLIC_PREFIXES bypass:
            the entire auth chain was skipped for public prefixes, so
            garbage tokens got pass-through behavior and route existence
            could be probed via the timing side channel).

        Returns a ProblemDetail response on invalid presented credentials,
        or None when the request is anonymous or successfully authenticated.
        """
        # No credential presented -> anonymous public request, allowed.
        if not self._presented_credential(request):
            return None

        # Dev bypass — explicitly opt-in, never in prod. Mirrors _authenticate
        # so local workflows with a stale Bearer token on public routes work.
        if not RMI_AUTH_TOKEN and settings.dev_bypass_auth:
            log.warning("DEV MODE: Public auth bypassed via DEV_BYPASS_AUTH", path=request.url.path)
            return None

        # Tier 1 — shared secrets (X-API-Key, X-RMI-Key, Bearer <secret>)
        if self._has_valid_shared_secret(request):
            request.state.auth_method = "shared_secret"
            return None

        # Tier 1b — Redis-backed admin API keys (rmi:api_keys).
        if await self._has_valid_admin_api_key(request):
            request.state.auth_method = "shared_secret"
            return None

        # Tier 2 — per-user JWT (Authorization: Bearer or rmi_token cookie)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                from app.auth.jwt_validation import get_current_user

                user = await get_current_user(request)
            except Exception as exc:
                log.warning("jwt_validation_error path=%s err=%s", request.url.path, exc)
                user = None
            if user is not None:
                request.state.user = user
                request.state.auth_method = "jwt"
                return None
            # A validly-signed pending-2FA (pre_auth) token is a legitimate
            # credential that hasn't completed 2FA yet. On a PUBLIC route it
            # must pass through anonymously (the 2FA-completion endpoint at
            # /api/v1/auth/2fa/verify consumes it). It grants no user claims
            # and is rejected on protected routes by validate_token.
            try:
                from app.auth.jwt_validation import is_preauth_token

                if is_preauth_token(auth_header[7:].strip()):
                    return None
            except Exception as exc:
                log.warning("preauth_check_error path=%s err=%s", request.url.path, exc)

        # A credential was presented but is invalid — reject.
        log.warning(
            "Auth denied: invalid credentials on public route",
            path=request.url.path,
        )
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
        registered to the request's X-Consumer-ID.

        Comparisons use secrets.compare_digest (constant-time) — plain set
        membership leaks key equality via timing.
        """
        api_key = (request.headers.get("X-API-Key") or "").strip()
        if api_key:
            valid = {RMI_AUTH_TOKEN} | _consumer_keys_for(request)
            if _constant_time_in(api_key, valid):
                return True

        rmi_key = (request.headers.get("X-RMI-Key") or "").strip()
        if rmi_key:
            valid = {RMI_INTERNAL_KEY, RMI_AUTH_TOKEN} | _consumer_keys_for(request)
            if _constant_time_in(rmi_key, valid):
                return True

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            valid = {RMI_AUTH_TOKEN, RMI_INTERNAL_KEY} | _consumer_keys_for(request)
            if _constant_time_in(token, valid):
                return True

        return False

    async def _has_valid_admin_api_key(self, request: Request) -> bool:
        """Accept an X-API-Key that matches an active admin API key in the
        Redis ``rmi:api_keys`` store (created via the admin panel).

        This is the enforcement that makes admin-panel revocation effective:
        a key whose status was flipped to ``revoked`` is rejected here.
        """
        api_key = (request.headers.get("X-API-Key") or "").strip()
        if not api_key:
            return False
        return await _is_valid_admin_api_key(api_key)

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
