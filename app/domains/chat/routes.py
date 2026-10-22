"""Canonical chat route: ``POST /api/v1/chat``.

Mounted before the legacy ``app.routers.chat`` surface, so this contract wins
for the bare path while every legacy sub-path (``/chat/stream``, history, …)
keeps working.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.domains.chat.limits import UserRateLimiter
from app.domains.chat.schemas import ChatRequest, ChatResponse
from app.domains.chat.service import answer

log = get_logger(__name__)

router = APIRouter(tags=["chat"])
_limiter = UserRateLimiter()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _identity(body: ChatRequest, request: Request) -> str:
    return body.user_id or body.conversation_id or _client_ip(request)


@router.post("/api/v1/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Answer a question with grounded citations. Always 200 unless rate-limited."""
    allowed, retry_after = await _limiter.allow(_identity(body, request))
    if not allowed:
        log.warning("chat_rate_limited channel=%s", body.channel)
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit_exceeded", "limit_per_minute": _limiter.per_minute},
            headers={"Retry-After": str(retry_after)},
        )
    return await answer(body)
