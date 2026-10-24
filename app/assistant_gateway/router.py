# ruff: noqa: TC001
"""FastAPI surface for the page assistant gateway.

Two paths, one handler:

* ``/api/v1/assistant/chat/completions`` — native path, mounted with ``router``
* ``/v1/chat/completions`` — OpenAI-compatible path, mounted via ``EXTRA_ROUTERS``

Neither path accepts a provider key, a tool key, or a client-selected model.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.assistant_gateway.aliases import ALIASES
from app.assistant_gateway.models import ChatCompletionRequest
from app.assistant_gateway.policy import PolicyViolation
from app.assistant_gateway.proxy import UpstreamError
from app.assistant_gateway.ratelimit import client_key
from app.assistant_gateway.responses import error_body, models_body
from app.assistant_gateway.service import AssistantService

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
openai_router = APIRouter(prefix="/v1", tags=["assistant"])
EXTRA_ROUTERS = [openai_router]


@lru_cache(maxsize=1)
def get_service() -> AssistantService:
    """Process-wide service (shares one limiter across requests)."""
    return AssistantService()


def _rate_headers(window: dict[str, int]) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(window.get("limit", 0)),
        "X-RateLimit-Remaining": str(window.get("remaining", 0)),
        "X-RateLimit-Reset": str(window.get("reset", 0)),
    }


async def chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
    alias: str | None = None,
    service: AssistantService = Depends(get_service),
) -> Any:
    """OpenAI-compatible chat completions, scoped to a page alias."""
    key = client_key(dict(request.headers), request.client.host if request.client else None)
    try:
        prepared = await service.prepare(payload, key, alias)
    except PolicyViolation as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.detail),
            headers=_rate_headers(exc.headers),
        )
    headers = _rate_headers(prepared.headers)
    if payload.stream:
        return StreamingResponse(
            service.stream(prepared, payload),
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    try:
        body = await service.complete(prepared, payload)
    except UpstreamError as exc:
        return JSONResponse(
            status_code=502,
            content=error_body("upstream_error", str(exc)),
            headers=headers,
        )
    return JSONResponse(content=body, headers=headers)


async def list_aliases() -> dict[str, Any]:
    """Public alias/tool matrix — names only, never keys or endpoints."""
    return {
        "aliases": [
            {
                "alias": spec.name,
                "product": spec.product,
                "tools": list(spec.tools),
                "context_fields": list(spec.context_fields),
                "requires_citations": spec.requires_citations,
            }
            for spec in ALIASES.values()
        ]
    }


async def health() -> dict[str, str]:
    """Liveness for the gateway itself (no upstream call)."""
    return {"status": "ok", "service": "assistant-gateway"}


router.add_api_route("/chat/completions", chat_completions, methods=["POST"])
router.add_api_route("/aliases", list_aliases, methods=["GET"])
router.add_api_route("/health", health, methods=["GET"])
openai_router.add_api_route("/chat/completions", chat_completions, methods=["POST"])
openai_router.add_api_route("/models", models_body, methods=["GET"])
