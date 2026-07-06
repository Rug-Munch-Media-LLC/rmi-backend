"""RMI Backend - typed error hierarchy + global error handlers.

Phase 1 of AUDIT-2026-Q3.md item P1.10.

The codebase had 2,532 ``except Exception:`` blocks and zero AppError class.
This module provides a strict typed hierarchy that handlers, routers, and
services should use instead of bare exception catches.

Two responsibilities coexist here:
  1. The ``AppError`` hierarchy (this file's top).
  2. ``register_error_handlers`` — used by ``app/lifespan.py`` to install
     StarletteHTTPException / ValueError / Exception handlers on the FastAPI
     app. Per-class handlers from ``app/error_handlers.py`` (404, AppError,
     validation, 500) coexist with these and the more specific class wins.
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from fastapi import (  # noqa: TC002 - runtime use for FastAPI decorator + inspect.signature
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── AppError hierarchy ──────────────────────────────────────────────


class AppError(Exception):
    """Base class for all RMI application errors.

    Subclasses set:
      - ``status_code``: HTTP status to return (default 500)
      - ``code``: short machine-readable string (e.g. "auth.invalid_token")
      - ``message``: human-readable description

    Carries an optional ``context`` dict that the global handler forwards to
    Sentry without code changes.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        status_code: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.context: dict[str, Any] = context or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "context": self.context,
        }


class AuthError(AppError):
    """401 — authentication or authorization failure."""

    status_code = 401
    code = "auth.unauthorized"

    def __init__(self, message: str = "unauthorized", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class RateLimitError(AppError):
    """429 — too many requests."""

    status_code = 429
    code = "ratelimit.exceeded"


class ValidationError(AppError):
    """400 — request body or params failed validation.

    Note: this is RMI's typed app-level ValidationError. Pydantic
    ValidationError lives in pydantic and is handled separately by
    app.error_handlers._register_validation. Importing pydantic's
    ValidationError under a different alias in services is fine.
    """

    status_code = 400
    code = "validation.failed"


class NotFoundError(AppError):
    """404 — resource does not exist."""

    status_code = 404
    code = "not_found"


class UpstreamError(AppError):
    """502 — an external service we depend on failed."""

    status_code = 502
    code = "upstream.failed"


# ── Handler registration (used by app/lifespan.py) ──────────────────


def register_error_handlers(app: FastAPI, debug: bool = False) -> None:
    """Register global exception handlers on the FastAPI app.

    Per-class AppError handler is registered first so the more-specific
    handler fires before the generic Exception handler below.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        body = exc.to_dict()
        body["request_id"] = request_id
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.detail,
                "code": exc.status_code,
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        response = {
            "error": "Internal server error",
            "code": 500,
            "request_id": request_id,
        }
        if debug:
            response["traceback"] = traceback.format_exc().split("\n")
            response["error"] = str(exc)
        return JSONResponse(status_code=500, content=response)

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse(
            status_code=400,
            content={
                "error": str(exc),
                "code": 400,
                "request_id": request_id,
            },
        )
