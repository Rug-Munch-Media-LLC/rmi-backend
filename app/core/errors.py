"""RMI Backend - typed error hierarchy + RFC 7807 global error handlers.

Phase 1 of AUDIT-2026-Q3.md item P1.10.
Win #11 from the fleet audit — standardised RFC 7807 Problem Detail responses.

The codebase had 2,532 ``except Exception:`` blocks and zero AppError class.
This module provides a strict typed hierarchy that handlers, routers, and
services should use instead of bare exception catches.

Two responsibilities coexist here:
  1. The ``AppError`` hierarchy (this file's top).
  2. ``register_error_handlers`` — used by ``app/lifespan.py`` to install
     RFC 7807 exception handlers on the FastAPI app.  ``app/error_handlers.py``
     also registers handlers but delegates to this module for the actual
     AppError / RequestValidationError / 500 catch-all dispatch.
"""

from __future__ import annotations

import traceback
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.problem_details import ProblemDetail

if TYPE_CHECKING:
    from fastapi import (
        FastAPI,
        Request,
    )

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
    """401 — authentication failure (missing/invalid credentials)."""

    status_code = 401
    code = "auth.unauthorized"

    def __init__(self, message: str = "unauthorized", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class AuthorizationError(AppError):
    """403 — authenticated but not permitted to access the resource."""

    status_code = 403
    code = "auth.forbidden"

    def __init__(self, message: str = "forbidden", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class PaymentRequiredError(AppError):
    """402 — payment required (x402 gate)."""

    status_code = 402
    code = "payment.required"

    def __init__(self, message: str = "payment required", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ConflictError(AppError):
    """409 — resource conflict (e.g. duplicate, version mismatch)."""

    status_code = 409
    code = "conflict"

    def __init__(self, message: str = "conflict", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class RateLimitError(AppError):
    """429 — too many requests."""

    status_code = 429
    code = "ratelimit.exceeded"


class ValidationError(AppError):
    """400 — request body or params failed validation.

    Note: this is RMI's typed app-level ValidationError. Pydantic
    ValidationError lives in pydantic and is handled separately by
    the RequestValidationError handler.  Importing pydantic's
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


class ExternalAPIError(AppError):
    """502 — an external API call failed (alias for semantic clarity)."""

    status_code = 502
    code = "external_api.failed"


class ServiceUnavailableError(AppError):
    """503 — service temporarily unavailable (e.g. backend dependency down)."""

    status_code = 503
    code = "service.unavailable"


# ── RFC 7807 helpers ────────────────────────────────────────────────


def _build_problem(
    request: Request,
    status: int,
    detail: str,
    *,
    code: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> ProblemDetail:
    """Build a ProblemDetail with request context filled in."""
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or str(uuid.uuid4())
    return ProblemDetail.create(
        status=status,
        detail=detail,
        code=code,
        instance=str(request.url),
        request_id=request_id,
        errors=errors,
    )


# ── Handler registration (used by app/lifespan.py) ──────────────────


def register_error_handlers(app: FastAPI, debug: bool = False) -> None:
    """Register RFC 7807 exception handlers on the FastAPI app.

    Registration order matters — more-specific handlers are registered
    before generic ones so FastAPI dispatches to the right handler.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        pd = _build_problem(
            request,
            status=exc.status_code,
            detail=str(exc) or exc.code,
            code=exc.code,
        )
        return pd.as_response()

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors: list[dict[str, Any]] = []
        for err in exc.errors():
            entry: dict[str, Any] = {
                "field": ".".join(str(loc) for loc in err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            if "input" in err:
                entry["value"] = str(err["input"])
            field_errors.append(entry)

        detail = "Request validation failed"
        if field_errors:
            detail += f": {field_errors[0]['field']} — {field_errors[0]['message']}"

        pd = _build_problem(
            request,
            status=422,
            detail=detail,
            code="validation.request_invalid",
            errors=field_errors,
        )
        return pd.as_response()

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        pd = _build_problem(request, status=exc.status_code, detail=detail)
        return pd.as_response()

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        pd = _build_problem(
            request,
            status=400,
            detail=str(exc),
            code="validation.value_error",
        )
        return pd.as_response()

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        pd = _build_problem(
            request,
            status=500,
            detail="Internal server error",
            code="internal_error",
        )
        # Include debug info for non-production environments
        if debug:
            body = pd.model_dump()
            body["debug"] = {
                "exception": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc().split("\n"),
            }
            return JSONResponse(status_code=500, content=body)
        return pd.as_response()
