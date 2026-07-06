"""RMI Backend - Auth middleware and API key verification."""

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

RMI_AUTH_TOKEN = os.getenv("RMI_AUTH_TOKEN", "")

PUBLIC_WRITE_PREFIXES = [
    "/api/v1/auth/",
    "/api/v1/x402/",
    "/api/v1/x402-tools/",
    "/api/v1/x402-databus/",
    "/api/v1/databus/",
    "/api/v1/alerts/",
    "/api/v1/admin/",
    "/api/v1/content/",
    "/api/v1/bulletin/",
    "/api/v1/rag/permanence/",
    "/api/v1/token/",
    "/api/v1/ai/",
    "/api/v1/premium/",
    "/api/v1/rag/",
    "/api/v1/protect/",
    "/api/v1/wallet-manager/",
]

# Auth bypass paths
PUBLIC_GET_PREFIXES = [
    "/api/v1/token/",
    "/api/v1/databus/",
    "/api/v1/alerts/",
    "/api/v1/x402-databus/",
    "/api/v1/x402-tools/",
]


def is_public_path(path: str, method: str) -> bool:
    """Check if a path is publicly accessible without auth."""
    if path in ("/health", "/ready", "/docs", "/openapi.json", "/redoc", "/", "/favicon.ico"):
        return True
    if path.startswith("/ws/") or not path.startswith("/api/"):
        return True
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        return any(path.startswith(p) for p in PUBLIC_WRITE_PREFIXES)
    return True  # GET/HEAD always public


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        if is_public_path(path, method):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        if RMI_AUTH_TOKEN and api_key != RMI_AUTH_TOKEN:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized - valid X-API-Key header required for write operations"},
            )
        return await call_next(request)
