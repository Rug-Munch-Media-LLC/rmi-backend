"""RMI Backend - Core Middleware."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import Request

# ═══════════════════════════════════════════════════════════════
# Rate-limit config
# ═══════════════════════════════════════════════════════════════
CACHEABLE_TOOLS = {"token_price", "token_metadata", "wallet_tokens", "entity_intel"}

# ═══════════════════════════════════════════════════════════════
# Payload size limit
# ═══════════════════════════════════════════════════════════════
MAX_PAYLOAD_SIZE = 1_048_576  # 1MB for standard JSON APIs

async def cache_middleware(request: Request, call_next) -> Any:
    """Check Redis cache before executing tool. Store result after."""
    path = request.url.path
    if not path.startswith("/api/v1/x402-tools/"):
        return await call_next(request)
    if request.method != "POST":
        return await call_next(request)

    tool = path.rstrip("/").split("/")[-1]
    if tool not in CACHEABLE_TOOLS:
        return await call_next(request)

    try:
        body = await request.body()
        params = json.loads(body) if body else {}
    except (json.JSONDecodeError):
        return await call_next(request)

    try:
        from app.routers.x402_advanced_tools import get_cached, set_cached

        cached = get_cached(tool, params)
        if cached:
            return JSONResponse(content=cached, headers={"X-Cache": "HIT", "X-Cache-TTL": "60"})
    except (AttributeError, ImportError, json.JSONDecodeError):
        pass

    response = await call_next(request)
    if response.status_code == 200:
        try:
            resp_body = b""
            async for chunk in response.body_iterator:
                resp_body += chunk
            result = json.loads(resp_body)
            set_cached(tool, params, result)
            return JSONResponse(
                content=result,
                status_code=response.status_code,
                headers={**dict(response.headers), "X-Cache": "MISS"},
            )
        except (json.JSONDecodeError):
            pass
    return response

async def emergency_lockdown_middleware(request: Request, call_next) -> Any:
    """Check for emergency lockdown status. Block non-admin routes if active."""
    if request.url.path in [
        "/health",
        "/api/v1/admin/emergency-lockdown",
        "/api/v1/admin/emergency-status",
    ]:
        return await call_next(request)

    try:
        import redis.asyncio as redis_lib

        try:
            from app.config import settings as _s
            _host, _port, _pass = _s.redis_host, _s.redis_port, _s.redis_password
        except Exception:
            _host, _port, _pass = "localhost", 6379, ""
        r = redis_lib.Redis(
            host=_host,
            port=_port,
            password=_pass,
            decode_responses=True,
        )
        is_locked = await r.exists("rmi:emergency_lockdown")
        if is_locked:
            auth_header = request.headers.get("Authorization", "")
            session_token = request.headers.get("X-Admin-Session", "")
            if not auth_header and not session_token:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "Service Unavailable",
                        "detail": "System is in emergency lockdown.",
                    },
                )
    except (ConnectionError, OSError, TimeoutError):
        pass
    return await call_next(request)

async def hsts_middleware(request: Request, call_next) -> Any:
    """Force HTTPS and prevent protocol downgrade attacks."""
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response

async def request_id_middleware(request: Request, call_next) -> Any:
    """Generate unique request ID for log correlation."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response

async def payload_size_limit_middleware(request: Request, call_next) -> Any:
    """Enforce 1MB payload limit on non-upload routes."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_PAYLOAD_SIZE:
        if not request.url.path.startswith("/api/v1/admin/backend/upload/"):
            return JSONResponse(
                status_code=413,
                content={"error": "Payload Too Large", "detail": "Maximum payload size is 1MB"},
            )
    return await call_next(request)

async def secure_cookie_middleware(request: Request, call_next) -> Any:
    """Enforce secure cookie flags for admin sessions."""
    response = await call_next(request)
    if "set-cookie" in response.headers:
        cookie_val = response.headers["set-cookie"]
        if "HttpOnly" not in cookie_val:
            cookie_val += "; HttpOnly"
        if "Secure" not in cookie_val:
            cookie_val += "; Secure"
        if "SameSite=Strict" not in cookie_val and "SameSite=Lax" not in cookie_val:
            cookie_val += "; SameSite=Strict"
        response.headers["set-cookie"] = cookie_val
    return response
