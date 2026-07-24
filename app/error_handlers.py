"""RMI Backend - exception handlers.

Clean RFC 7807 JSON error responses for 404, 500, AppError, etc.  Each
handler is isolated — one failure doesn't break the others.

This module is called by ``app.factory.create_app()`` and delegates to the
core RFC 7807 handlers from ``app.core.errors`` for AppError / validation /
generic error dispatch.  The per-class handlers here (404, 500, AppError)
coexist with the lifespan-registered handlers; the more-specific class wins.
"""

from __future__ import annotations

from fastapi import (  # noqa: TC002 - runtime use (matches app/core/errors.py + pre-existing project-wide pattern)
    FastAPI,
    Request,
)
from fastapi.responses import JSONResponse

from app.core.errors import (
    AppError,
    AuthError,
    AuthorizationError,
    ConflictError,
    ExternalAPIError,
    NotFoundError,
    PaymentRequiredError,
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.problem_details import ProblemDetail

log = get_logger(__name__)

def register_error_handlers(app: FastAPI) -> None:
    """Register every error handler. Each is isolated."""
    _register_404(app)
    _register_validation(app)
    _register_500(app)
    _register_apperror(app)

def _register_404(app: FastAPI) -> None:
    @app.exception_handler(404)
    async def _not_found(request: Request, _exc: Exception) -> JSONResponse:
        pd = ProblemDetail.create(
            status=404,
            detail="Not found",
            code="not_found",
            instance=str(request.url),
            request_id=getattr(request.state, "request_id", None),
        )
        return pd.as_response()

    log.info("error_handler_registered name=not_found")

def _register_validation(app: FastAPI) -> None:
    """422 - Pydantic validation errors.  Return structured RFC 7807 details.

    The core ``app.core.errors.register_error_handlers`` also registers a
    RequestValidationError handler — this module-level registration is a
    secondary guard in case the lifespan handler was skipped.
    """
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = []
        for err in exc.errors():
            entry = {
                "field": ".".join(str(loc) for loc in err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            field_errors.append(entry)

        pd = ProblemDetail.create(
            status=422,
            detail="Request validation failed",
            code="validation.request_invalid",
            instance=str(request.url),
            request_id=getattr(request.state, "request_id", None),
            errors=field_errors,
        )
        return pd.as_response()

    log.info("error_handler_registered name=validation")

def _register_500(app: FastAPI) -> None:
    """Generic 500 fallback for non-AppError exceptions.

    AppError instances are routed by the more-specific handler installed in
    _register_apperror, so this only fires for plain Exception subclasses.
    """

    @app.exception_handler(Exception)
    async def handle_generic_exception(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception path=%s", request.url.path)
        pd = ProblemDetail.create(
            status=500,
            detail="Internal server error",
            code="internal_error",
            instance=str(request.url),
            request_id=getattr(request.state, "request_id", None),
        )
        return pd.as_response()

    log.info("error_handler_registered name=internal_error")

def _register_apperror(app: FastAPI) -> None:
    """AppError → RFC 7807 JSON via its declared status_code."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        pd = ProblemDetail.create(
            status=exc.status_code,
            detail=str(exc) or exc.code,
            code=exc.code,
            instance=str(request.url),
            request_id=getattr(request.state, "request_id", None),
        )
        return pd.as_response()

    log.info(
        "error_handler_registered name=AppError handlers=%s",
        sorted(
            {
                AuthError.__name__,
                AuthorizationError.__name__,
                ConflictError.__name__,
                ExternalAPIError.__name__,
                NotFoundError.__name__,
                PaymentRequiredError.__name__,
                ServiceUnavailableError.__name__,
                UpstreamError.__name__,
                ValidationError.__name__,
            }
        ),
    )
