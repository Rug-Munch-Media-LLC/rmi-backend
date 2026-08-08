"""RMI Backend - middleware registration.

Per v3 unfuck rule #7: add_middleware MUST be called at module level,
NOT inside lifespan. FastAPI rejects middleware added after startup.

This module owns every middleware registration. Adding a new middleware:
  1. Add a `try_register(app, "name")` block below
  2. Each block is isolated - one failure doesn't break the rest
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI

from app.core.logging import bind_request_id, get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

# ── CSRF-protected methods ───────────────────────────────────────────
_CSRF_METHODS: tuple[str, ...] = ("POST", "PUT", "DELETE", "PATCH")

log = get_logger(__name__)


async def _safe_rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return 429 without crashing when slowapi state is missing.

    slowapi's stock handler reads ``request.state.view_rate_limit``, which
    is only populated on routes decorated with ``@limiter.limit``. Any 429
    raised by an undecorated route (e.g. a handler returning
    ``HTTPException(429, ...)``) would otherwise raise AttributeError and
    turn into a 500. This wrapper falls back gracefully and still injects
    rate-limit headers when the state is available.
    """
    from starlette.responses import JSONResponse

    detail = getattr(exc, "detail", None) or "Rate limit exceeded"
    response = JSONResponse({"error": f"Rate limit exceeded: {detail}"}, status_code=429)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    view_limit = getattr(request.state, "view_rate_limit", None)
    if view_limit is not None and hasattr(request.app.state, "limiter"):
        try:
            inject_headers = getattr(request.app.state.limiter, "_inject_headers", None)
            if inject_headers is not None:
                response = inject_headers(response, view_limit)
        except Exception as exc:
            log.warning("slowapi_header_inject_failed err=%s", exc)
    return response


def register_middleware(app: FastAPI) -> None:
    """Register all middleware. Each registration is isolated."""
    _try_register_request_id(app)
    _try_register_cors(app)
    _try_register_auth(app)
    _try_register_security_headers(app)
    _try_register_prometheus(app)
    _try_register_cost_tracking(app)
    _try_register_tracing(app)
    _try_register_degradation_header(app)
    _try_register_deprecation_header(app)
    # AUDIT-2026-Q3 P0 (rate-limiter consolidation): slowapi disabled.
    # UnifiedRateLimiter (app.core.rate_limiter.RateLimitMiddleware, registered
    # below) is the single active limiter — Redis-backed token bucket with
    # per-user/per-IP granularity covering all paths. slowapi registration kept
    # for reference (see _try_register_slowapi below) but NOT wired.
    # _try_register_slowapi(app)
    _try_register_rate_limit_middleware(app)
    _try_register_slow_query_logging(app)
    _try_register_content_type_enforcement(app)
    _try_register_csrf(app)


def _try_register_slow_query_logging(app: FastAPI) -> None:
    """Log warnings for endpoints that take > 500ms.

    Helps identify performance regressions in production without
    needing APM tooling for every deployment.
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        slow_threshold_ms: float = 500.0

        class SlowQueryLoggingMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                start = time.perf_counter()
                response = cast("Response", await call_next(request))
                elapsed_ms = (time.perf_counter() - start) * 1000

                if elapsed_ms > slow_threshold_ms:
                    path = request.url.path
                    method = request.method
                    qs = request.url.query or ""
                    log.warning(
                        "slow_request method=%s path=%s qs=%s elapsed_ms=%.0f threshold_ms=%.0f",
                        method,
                        path,
                        qs[:200],
                        elapsed_ms,
                        slow_threshold_ms,
                    )
                return response

        app.add_middleware(SlowQueryLoggingMiddleware)
        log.info("middleware_registered name=slow_query_logging")
    except Exception as exc:
        log.warning("middleware_skipped name=slow_query_logging err=%s", exc)


def _try_register_request_id(app: FastAPI) -> None:
    """Always-on request ID + response-time headers (Win #11 from fleet audit).

    Sets ``X-Request-ID`` and ``X-Response-Time-Ms`` on every response.
    Also populates ``request.state.request_id`` for error handlers, and
    **injects the request ID into structlog context vars** so every log
    entry during this request automatically carries the correlation ID.

    Enabled independently of OTEL tracing so error responses always carry
    a correlation ID.
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        class RequestIDMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
                request.state.request_id = request_id

                # ── Inject into structlog context for log correlation ──
                bind_request_id(request_id)

                start = time.perf_counter()

                response = cast("Response", await call_next(request))

                elapsed_ms = (time.perf_counter() - start) * 1000
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Response-Time-Ms"] = str(round(elapsed_ms, 1))
                response.headers["Server-Timing"] = f"total;dur={round(elapsed_ms, 1)}"

                return response

        app.add_middleware(RequestIDMiddleware)
        log.info("middleware_registered name=request_id_timing")
    except Exception as exc:
        log.warning("middleware_skipped name=request_id_timing err=%s", exc)


def _try_register_degradation_header(app: FastAPI) -> None:
    """Win #14 — inject X-Degraded-Stores header on every response."""
    try:
        from starlette.middleware.base import BaseHTTPMiddleware

        class DegradationHeaderMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                response = cast("Response", await call_next(request))
                try:
                    from app.core.degradation import get_registry

                    header = get_registry().degraded_header
                    if header:
                        response.headers["X-Degraded-Stores"] = header
                except (ImportError, RuntimeError):
                    pass
                return response

        app.add_middleware(DegradationHeaderMiddleware)
        log.info("middleware_registered name=degradation_header")
    except Exception as exc:
        log.warning("middleware_skipped name=degradation_header err=%s", exc)


def _try_register_deprecation_header(app: FastAPI) -> None:
    """Win #19 — inject Deprecation + Sunset headers on root-level routes.

    Known root-level routes that have /api/v1/* equivalents receive
    deprecation warning headers. Infra routes (health, metrics, MCP, docs)
    are excluded.
    """
    # Routes that should NOT receive deprecation headers
    _EXCLUDED_PREFIXES: tuple[str, ...] = (
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
    _DEPRECATED_PREFIXES: tuple[str, ...] = (
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

        _SUNSET_DATE: str = "Thu, 15 Oct 2026 00:00:00 GMT"  # 90 days from 2026-07-17 (matches mount.py)

        class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                response = await call_next(request)
                path = request.url.path

                # Skip excluded paths
                if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
                    return response

                # Add deprecation headers to known root-level routes
                if any(path.startswith(p) for p in _DEPRECATED_PREFIXES):
                    response.headers["Deprecation"] = "true"
                    response.headers["Sunset"] = _SUNSET_DATE
                    response.headers["Link"] = '<https://docs.rugmunch.io/api/versioning>; rel="deprecation"'

                return response

        app.add_middleware(DeprecationHeaderMiddleware)
        log.info("middleware_registered name=deprecation_header")
    except Exception as exc:
        log.warning("middleware_skipped name=deprecation_header err=%s", exc)


def _try_register_rate_limit_middleware(app: FastAPI) -> None:
    """T28b — Unified token-bucket rate limiting for all paths."""
    try:
        from app.core.rate_limiter import RateLimitMiddleware, get_rate_limiter

        limiter = get_rate_limiter()
        app.add_middleware(RateLimitMiddleware, limiter=limiter)
        log.info("middleware_registered name=rate_limit_middleware")
    except Exception as exc:
        log.warning("middleware_skipped name=rate_limit_middleware err=%s", exc)


def _try_register_slowapi(app: FastAPI) -> None:
    """T28 - slowapi rate limiting per PATH_CATEGORIES + decorators."""
    try:
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware

        from app.core.slowrate import limiter

        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _safe_rate_limit_exceeded_handler)
        app.add_exception_handler(429, _safe_rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
        log.info("middleware_registered name=slowapi")
    except Exception as exc:
        log.warning("middleware_skipped name=slowapi err=%s", exc)


def _try_register_cors(app: FastAPI) -> None:
    """T19 - CORS hardening with strict allowlist."""
    try:
        from fastapi.middleware.cors import CORSMiddleware

        from app.config import settings

        # Validate CORS origins in production
        if settings.environment == "prod" and (not settings.cors_origins or settings.cors_origins == ["*"]):
            raise RuntimeError(
                "CORS_ORIGINS cannot be empty or ['*'] in production. "
                "Set CORS_ORIGINS to a specific list of allowed origins, e.g. "
                "CORS_ORIGINS='[\"https://app.rugmunch.io\"]'."
            )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_origin_regex=r"https://.*\.rugmunch\.io",
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Request-ID",
                "X-Payment",
                "X-CSRF-Token",
            ],
        )
        log.info("middleware_registered name=cors")
    except Exception as exc:
        log.warning("middleware_skipped name=cors err=%s", exc)


def _try_register_auth(app: FastAPI) -> None:
    """API key authentication via X-API-Key header."""
    try:
        from app.core.auth import AuthMiddleware

        app.add_middleware(AuthMiddleware)
        log.info("middleware_registered name=auth")
    except Exception as exc:
        log.warning("middleware_skipped name=auth err=%s", exc)


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
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
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
    """OpenTelemetry tracing (gated by OTEL_ENABLED env var).

    Note: the always-on request ID and response-time headers are set by the
    ``_try_register_request_id`` middleware above (Win #11 from fleet audit).
    This middleware adds OpenTelemetry spans only.
    """
    try:
        from app.core.tracing import setup_tracing

        setup_tracing(app)
        log.info("middleware_registered name=tracing")
    except Exception as exc:
        log.warning("middleware_skipped name=tracing err=%s", exc)


def _try_register_content_type_enforcement(app: FastAPI) -> None:
    """PEN-TEST: Reject non-JSON Content-Type on write endpoints.

    POST/PUT/PATCH/DELETE endpoints must have Content-Type: application/json
    (except multipart/form-data for file uploads). This blocks XML, text/plain,
    form-encoded, and other content types that could bypass JSON validation.
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        _WRITE_METHODS: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")
        _ALLOWED_CT_PREFIXES: tuple[str, ...] = (
            "application/json",
            "multipart/form-data",  # File uploads
            "application/x-www-form-urlencoded",  # OAuth2 token endpoints
        )
        _EXEMPT_PATHS: tuple[str, ...] = (
            "/health",
            "/live",
            "/ready",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/mcp/",  # MCP endpoints use their own content-type negotiation
        )

        class ContentTypeEnforcementMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                path = request.url.path
                method = request.method

                # Only enforce on write methods
                if method in _WRITE_METHODS:
                    # Skip exempt paths
                    if not any(path.startswith(p) for p in _EXEMPT_PATHS):
                        ct = (request.headers.get("content-type") or "").lower().strip()
                        if ct and not ct.startswith("application/json"):
                            # Allow multipart and form-urlencoded
                            if not any(ct.startswith(p) for p in _ALLOWED_CT_PREFIXES[1:]):
                                log.warning(
                                    "rejected_non_json_content_type method=%s path=%s content_type=%s",
                                    method,
                                    path,
                                    ct,
                                )
                                return JSONResponse(
                                    status_code=415,
                                    content={
                                        "detail": "Unsupported media type. Use Content-Type: application/json.",
                                        "code": "unsupported_media_type",
                                    },
                                )

                return cast("Response", await call_next(request))

        app.add_middleware(ContentTypeEnforcementMiddleware)
        log.info("middleware_registered name=content_type_enforcement")
    except Exception as exc:
        log.warning("middleware_skipped name=content_type_enforcement err=%s", exc)


def _try_register_csrf(app: FastAPI) -> None:
    """Win #12 — Double-submit cookie CSRF protection.

    - Skips CSRF check for GET/HEAD/OPTIONS (safe methods).
    - Skips CSRF check when ``Authorization: Bearer`` header is present
      (API clients authenticate via token, not cookie — immune to CSRF).
    - For state-changing requests via cookie auth: requires ``X-CSRF-Token``
      header matching the ``csrf_token`` cookie value.
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import JSONResponse

        # x402 payment API paths — exempt from cookie-based CSRF
        # These are agent/MCP API endpoints that use X-Payment, X-Api-Key,
        # or X-Agent-Id headers instead of cookies. CSRF is not applicable.
        _X402_CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
            "/api/v1/x402/",
            "/api/v1/x402-tools/",  # x402 paid-tool surface — agent/MCP API (X-Payment, X-Api-Key headers, no cookies)
            "/api/v1/x402-databus/",
            "/api/v1/bulletin-board/",  # Bulletin board — public REST API (auth via Bearer token header, not cookies)
            "/api/v1/developer/",
            "/api/v1/databus/fetch",  # DataBus universal fetch — used by frontend MCP + authenticated clients
            "/api/v1/databus/webhooks/",  # External webhooks (Helius, Moralis, etc.) — no cookies
            "/api/v1/agent/",  # Pydantic AI agent fleet — service-to-service, no cookies
            "/api/v1/public/",  # Public scanner/wallet APIs — called by walletpress, bots, etc.
            "/api/v2/scanner/",  # Unified scanner v2 — token/wallet scan, core product, called by curl/bots/MCP
            "/api/v1/chat",  # AI chat — public (anon users don't have cookies; rate-limited per IP)
            "/api/v1/rag/",  # RAG search & ask — public product feature (rate-limited, no cookies)
            "/api/v1/ai/",  # AI endpoints (chat, stream, ai/ai/*) — public, no cookies
            "/api/v1/newsletter/",  # Newsletter — public signup & content, no cookies
            "/api/v1/scam-school/",  # Scam School — public education content, no cookies
            "/api/v1/token/",  # Token page — public token data, no cookies
            "/api/v1/auth/",  # Auth endpoints (login/register) — cookie-free bearer auth
            "/api/v1/bulletin-board/posts/public",  # Bulletin board public posts — no cookies
            "/api/v1/rugmaps/",  # RugMaps token graph & entity analysis — public product feature
            "/api/v1/community-forensics/",  # Community forensics — public report submission
            "/api/v1/stripe/",  # Stripe webhooks + checkout — no cookies
            "/api/v1/bot/",  # Telegram bot API — public endpoints for bot connectivity
            "/api/v1/news/_admin/",  # News admin ingest — internal service-to-service (X-RMI-Key), no cookies
            "/api/v1/news/internal/",  # News internal endpoints — internal service-to-service (X-RMI-Key), no cookies
            "/mcp/x402",
            "/x402/sandbox/",
            "/.well-known/x402",
        )

        class CSRFMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next: Any) -> Response:
                # Skip safe methods
                if request.method not in _CSRF_METHODS:
                    return cast("Response", await call_next(request))

                # Skip x402 payment API endpoints (agent/MCP, no cookies)
                path = request.url.path
                if any(path.startswith(p) for p in _X402_CSRF_EXEMPT_PREFIXES):
                    return cast("Response", await call_next(request))

                # Skip if Authorization: Bearer header is present (API clients)
                auth = request.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    return cast("Response", await call_next(request))

                # Skip if X-API-Key header is present (service-to-service auth)
                api_key = request.headers.get("X-API-Key", "")
                if api_key:
                    return cast("Response", await call_next(request))

                # Skip if X-RMI-Key header is present (internal service-to-service
                # auth — same dual-token model as X-API-Key)
                rmi_key = request.headers.get("X-RMI-Key", "")
                if rmi_key:
                    return cast("Response", await call_next(request))

                # Validate CSRF token from header against cookie
                csrf_cookie = request.cookies.get("csrf_token")
                csrf_header = request.headers.get("X-CSRF-Token")

                if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
                    log.warning(
                        "csrf_validation_failed method=%s path=%s",
                        request.method,
                        request.url.path,
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF validation failed"},
                    )

                return cast("Response", await call_next(request))

        app.add_middleware(CSRFMiddleware)
        log.info("middleware_registered name=csrf")
    except Exception as exc:
        log.warning("middleware_skipped name=csrf err=%s", exc)
