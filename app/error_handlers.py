"""RMI Backend - exception handlers.

Clean JSON error responses for 404, 500, AppError, etc. Each
handler is isolated - one failure doesn't break the others.
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
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from app.core.logging import get_logger

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
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "path": request.url.path,
                "method": request.method,
                "hint": "RMI new backend. Check /docs for current API surface.",
            },
        )

    log.info("error_handler_registered name=not_found")


def _register_validation(app: FastAPI) -> None:
    """422 - Pydantic validation errors. Return structured details.

    Phase 3 of AUDIT-2026-Q3.md (router split) will wire RequestValidationError
    to RMI's typed ValidationError here. Until then, this is a logging stub.
    """
    log.info("error_handler_registered name=validation (pending phase 3)")


def _register_500(app: FastAPI) -> None:
    """Generic 500 fallback for non-AppError exceptions.

    AppError instances are routed by the more-specific handler installed in
    _register_apperror, so this only fires for plain Exception subclasses.
    """

    @app.exception_handler(Exception)
    async def handle_generic_exception(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "path": request.url.path,
                "type": type(exc).__name__,
            },
        )

    log.info("error_handler_registered name=internal_error")


def _register_apperror(app: FastAPI) -> None:
    """AppError → JSON via its declared status_code + to_dict().

    Import is at module top so a missing AppError is a hard ImportError
    at import time rather than a silently-swallowed runtime warning.
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        body = exc.to_dict()
        body["path"] = request.url.path
        body["type"] = type(exc).__name__
        return JSONResponse(status_code=exc.status_code, content=body)

    log.info(
        "error_handler_registered name=AppError handlers=%s",
        sorted(
            {
                AuthError.__name__,
                NotFoundError.__name__,
                ValidationError.__name__,
                UpstreamError.__name__,
            }
        ),
    )
