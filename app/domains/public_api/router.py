"""Public scan projection: cached, redacted, ETag-validated."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response
from starlette.requests import Request as StarletteRequest  # noqa: TC002 — FastAPI resolves it

from app.domains.public_api.redaction import public_scan
from app.domains.public_api.store import PublicScanSource, RedisScanCache
from app.domains.public_api.tiers import TIERS, PublicTier, resolve_tier

if TYPE_CHECKING:
    from collections.abc import Mapping

router = APIRouter(prefix="/public", tags=["public"])

_PROBLEM_MEDIA_TYPE = "application/problem+json"
# Token ids are bounded so a cache key can never be constructed from a path.
_ADDRESS = Path(min_length=1, max_length=128, pattern="^[A-Za-z0-9:_-]+$")
_CHAIN = Path(min_length=2, max_length=32, pattern="^[a-z0-9]+$")


def get_scan_source() -> PublicScanSource:
    return RedisScanCache()


def _tier(request: StarletteRequest) -> PublicTier:
    resolved = getattr(request.state, "public_tier", None)
    if isinstance(resolved, PublicTier):
        return resolved
    return resolve_tier(None, authenticated=False)


def _cache_headers(tier: PublicTier, *, fresh: bool) -> dict[str, str]:
    if fresh:
        return {"Cache-Control": "no-store"}
    return {
        "Cache-Control": (f"public, max-age={tier.cache_ttl_s}, stale-while-revalidate={tier.stale_while_revalidate_s}")
    }


def _etag(body: Mapping[str, object]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return f'W/"{hashlib.sha256(canonical.encode()).hexdigest()[:32]}"'


def _not_found(chain: str, address: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"title": "Not Found", "detail": "no cached scan for that token", "chain": chain, "address": address},
    )


@router.get("/scans/{chain}/{address}", response_model=None)
async def get_public_scan(
    request: StarletteRequest,
    chain: str = _CHAIN,
    address: str = _ADDRESS,
    fresh: bool = Query(False, description="Bypass the client cache; never triggers a scan"),
    if_none_match: Annotated[str | None, Header()] = None,
    source: PublicScanSource = Depends(get_scan_source),
) -> Response:
    """Cached scan, projected for the caller's tier and safe to cache at the edge."""
    tier = _tier(request)
    cached = await source.read(chain, address)
    if cached is None:
        raise _not_found(chain, address)

    body = public_scan(cached, depth=tier.scan_depth)
    etag = _etag(body)
    headers = {**_cache_headers(tier, fresh=fresh), "ETag": etag, "X-Public-Tier": tier.name}
    if not fresh and if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=json.dumps(body, default=str),
        media_type="application/json",
        headers=headers,
    )


@router.get("/tiers", response_model=None)
async def list_tiers() -> dict[str, dict[str, object]]:
    """The published quota table, so a client can plan against real numbers."""
    return {
        name: {
            "requests_per_min": tier.requests_per_min,
            "scan_depth": tier.scan_depth,
            "history_window_days": tier.history_window_days,
            "export_max_rows": tier.export_max_rows,
            "websocket_streams": tier.websocket_streams,
            "cache_ttl_s": tier.cache_ttl_s,
        }
        for name, tier in TIERS.items()
    }
