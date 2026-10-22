"""Mount unit for the chat domain — one entry in ``ROUTER_MODULES``.

Registration order inside this router IS the resolution order for FastAPI:

1. :mod:`app.domains.chat.routes` — the canonical ``POST /api/v1/chat``
   contract (``answer``/``citations``/``provider``/``fallback_used``).
2. ``app.routers.chat`` — the pre-existing handlers, unchanged. Every sub-path
   (``/stream``, history, stats) keeps working; its own ``POST /api/v1/chat``
   is shadowed by (1) and stays reachable at ``/api/v1/chat/legacy`` (3).
3. The alias rebuilt from the same endpoint objects — no handler duplication.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.core.logging import get_logger

log = get_logger(__name__)

LEGACY_PREFIX = "/api/v1/chat"
ALIAS_PREFIX = "/api/v1/chat/legacy"
_SKIP_METHODS = {"HEAD", "OPTIONS"}


def _alias_mount(source: APIRouter) -> APIRouter:
    """Re-register every legacy chat route one segment deeper."""
    alias = APIRouter()
    for route in source.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(LEGACY_PREFIX):
            continue
        alias.add_api_route(
            ALIAS_PREFIX + route.path[len(LEGACY_PREFIX) :],
            route.endpoint,
            methods=sorted(set(route.methods or set()) - _SKIP_METHODS) or ["POST"],
            name=f"legacy_{route.name}",
            response_model=route.response_model,
            status_code=route.status_code,
            response_class=route.response_class,
            dependencies=route.dependencies,
            summary=route.summary,
            tags=["chat-legacy"],
        )
    return alias


def _build() -> APIRouter:
    from app.domains.chat.routes import router as canonical

    router = APIRouter()
    router.include_router(canonical)
    try:
        from app.routers.chat import router as legacy

        router.include_router(legacy)
        router.include_router(_alias_mount(legacy))
        log.info("chat_legacy_mounted prefix=%s alias=%s", LEGACY_PREFIX, ALIAS_PREFIX)
    except Exception as exc:
        log.warning("chat_legacy_mount_failed err=%s", type(exc).__name__)
    return router


router = _build()
