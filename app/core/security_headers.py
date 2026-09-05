"""Shared security-header constants.

Single source of truth for the security headers applied to every response.
Used by ``SecurityHeadersMiddleware`` (app/middleware_setup.py) and by the
rate limiter's 429 short-circuit responses (app/core/rate_limiter.py) — the
limiter is registered outermost, so its early returns bypass the inner
headers middleware and must apply the headers themselves.
"""

from __future__ import annotations

from typing import Any

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": "default-src 'self'",
}


def apply_security_headers(response: Any, *, path: str | None = None) -> Any:
    """Apply the standard security headers to a response in place."""
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    # Docs pages get a slightly more permissive CSP (inline scripts/styles)
    if path in ("/docs", "/redoc", "/openapi.json"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        )
    return response
