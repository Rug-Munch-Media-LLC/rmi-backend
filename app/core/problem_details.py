"""RFC 7807 / RFC 9457 Problem Details for HTTP APIs.

Standard error envelope shared across all RMI services.

Usage:
    from app.core.problem_details import problem_response
    return problem_response(404, "not_found.post", detail="Post not found", instance="/api/v1/bulletin/123")
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def problem_response(
    status: int,
    code: str,
    detail: str = "",
    instance: str = "",
    errors: list[dict[str, Any]] | None = None,
    request_id: str = "",
) -> JSONResponse:
    """Return a JSONResponse formatted as RFC 7807 Problem Details.

    Args:
        status: HTTP status code (400–599).
        code: Machine-readable error code (e.g. "auth.invalid_token").
        detail: Human-readable explanation.
        instance: URI of the request that triggered the error.
        errors: Validation error details (for 422).
        request_id: Correlation ID for tracing.
    """
    body: dict[str, Any] = {
        "type": f"/errors/{code.replace('.', '/')}",
        "title": _STATUS_TITLES.get(status, "Error"),
        "status": status,
        "detail": detail or _STATUS_TITLES.get(status, "Error"),
        "instance": instance,
        "code": code,
    }
    if request_id:
        body["request_id"] = request_id
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status, content=body)
