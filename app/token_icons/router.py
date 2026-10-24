"""Token icon endpoints — on-demand resolution + self-hosted fallback PNG."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.token_icons.fallback import fallback_icon_url
from app.token_icons.palette import brand_rgb
from app.token_icons.png import solid_png
from app.token_icons.resolver import resolve_icon
from app.token_icons.schemas import IconResolution

router = APIRouter(tags=["token-icons"])

_FALLBACK_CACHE = "public, max-age=86400, immutable"


@router.get(
    "/tokens/icon-fallback/{chain}/{token}.png",
    summary="Branded fallback icon (generated, cached, never 404)",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def fallback_png(chain: str, token: str) -> Response:
    """Deterministic branded PNG used when a token has no usable icon."""
    return Response(
        content=solid_png(brand_rgb(chain, token)),
        media_type="image/png",
        headers={"Cache-Control": _FALLBACK_CACHE},
    )


@router.get(
    "/tokens/{chain}/{token}/icon",
    response_model=IconResolution,
    summary="Resolve a token icon (cached; validated https or branded fallback)",
)
async def token_icon(chain: str, token: str, refresh: bool = False) -> dict:
    """Resolve ``(chain, token)`` to a displayable icon URL."""
    return await resolve_icon(chain, token, refresh=refresh)


@router.get(
    "/tokens/{chain}/{token}/icon/fallback-url",
    summary="Deterministic fallback URL without resolving upstream",
)
def token_icon_fallback_url(chain: str, token: str) -> dict:
    """Return the fallback URL a client can use before/without resolution."""
    return {"chain": chain, "token": token, "icon_url": fallback_icon_url(chain, token)}
