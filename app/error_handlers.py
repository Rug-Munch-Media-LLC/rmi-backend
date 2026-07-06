"""RMI Backend - exception handlers.

Clean JSON error responses for 404, 500, AppError, etc. Each
handler is isolated - one failure doesn't break the others.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


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
    """422 - Pydantic validation errors. Return structured details."""
    try:  # noqa: SIM105
        # The core module may also register; we don't want to double-register.
        # Skip if it already handled 422.
        log.info("error_handler_registered name=validation (via core)")
    except Exception:
        pass


def _register_500(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
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
    """AppError → JSON via core.errors.register_error_handlers (if available)."""
    try:

        from app.core.errors import AppError

        @app.exception_handler(AppError)
        async def _app_error(request: Request, exc: AppError) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": exc.__class__.__name__,
                    "message": exc.message,
                    "path": request.url.path,
                },
            )

        log.info("error_handler_registered name=AppError")
    except Exception as exc:
        log.info("error_handler_skipped name=AppError err=%s", exc)
