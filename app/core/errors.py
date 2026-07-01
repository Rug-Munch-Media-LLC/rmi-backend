"""RMI Backend - Global error handlers with structured responses.

Registers FastAPI exception handlers that return consistent JSON error responses
with request IDs, tracebacks (dev only), and error codes.
"""

import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def register_error_handlers(app: FastAPI, debug: bool = False) -> None:
    """Register global exception handlers on the FastAPI app."""

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
