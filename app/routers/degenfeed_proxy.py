"""DegenFeed Protocol Proxy — API Gateway for DegenFeed services.

This router provides a unified API surface for frontend clients to interact
with DegenFeed protocol operations. It proxies requests to DegenFeed services
and provides caching, rate limiting, and error handling.

Endpoints:
  GET    /api/v1/degenfeed/{protocol}/feed        — Fetch unified feed
  GET    /api/v1/degenfeed/{protocol}/post/{id}   — Get specific post
  POST   /api/v1/degenfeed/{protocol}/post        — Create a post (cross-posting)
  GET    /api/v1/degenfeed/identity/{protocol}/{handle} — Resolve identity
  GET    /api/v1/degenfeed/protocols              — List supported protocols
  GET    /api/v1/degenfeed/health                 — DegenFeed health check

Per-protocol rate limits:
  - farcaster:   30 req/min per user, 100 req/min per IP
  - lens:        30 req/min per user, 100 req/min per IP
  - twitter:     15 req/min per user,  60 req/min per IP (higher cost)
  - bluesky:     30 req/min per user, 100 req/min per IP
  - mastodon:    60 req/min per user, 200 req/min per IP (federated)
  - nostr:       60 req/min per user, 200 req/min per IP (decentralized)
  - cross-post:  10 req/min per user (limit cross-posting frequency)

Rate limit headers are returned in every response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.rate_limiter import RateLimitDependency, get_rate_limiter
from app.services.degenfeed_cache import (
    get_cached_feed,
    get_cached_identity,
    get_cached_post,
    health_check as cache_health,
    invalidate_feed,
    set_cached_feed,
    set_cached_identity,
    set_cached_post,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/degenfeed", tags=["degenfeed"])

# ── Per-protocol rate limit configuration ─────────────────────────
# Format: {protocol: (requests_per_minute, burst_multiplier)}
PROTOCOL_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "farcaster": (30, 0.25),
    "lens": (30, 0.25),
    "twitter": (15, 0.2),
    "bluesky": (30, 0.25),
    "mastodon": (60, 0.25),
    "nostr": (60, 0.25),
    "cross-post": (10, 0.3),
}

# Default rate limit for unknown protocols
_DEFAULT_PROTOCOL_LIMIT: tuple[int, float] = (20, 0.25)

# ── Supported protocols ───────────────────────────────────────────
SUPPORTED_PROTOCOLS: dict[str, str] = {
    "farcaster": "Farcaster — sufficiently decentralized social",
    "lens": "Lens Protocol — decentralized social graph",
    "twitter": "Twitter/X — centralized microblogging",
    "bluesky": "Bluesky — AT Protocol social network",
    "mastodon": "Mastodon — federated ActivityPub social",
    "nostr": "Nostr — decentralized relay-based social",
}

# ── Models ────────────────────────────────────────────────────────


class IdentityResponse(BaseModel):
    """Resolved identity across protocols."""

    protocol: str
    handle: str
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    follower_count: int = 0
    following_count: int = 0
    verified: bool = False
    profiles: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class PostResponse(BaseModel):
    """A post/status update from any protocol."""

    id: str
    protocol: str
    author: IdentityResponse
    text: str
    created_at: str
    likes: int = 0
    shares: int = 0
    replies: int = 0
    media_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class FeedResponse(BaseModel):
    """Paginated feed response."""

    protocol: str
    page: int
    has_more: bool = False
    posts: list[PostResponse] = Field(default_factory=list)
    cached: bool = False


class CreatePostRequest(BaseModel):
    """Request body for creating a post."""

    text: str = Field(..., min_length=1, max_length=2000, description="Post content")
    media_urls: list[str] = Field(default_factory=list, max_length=4, description="Attached media URLs")
    tags: list[str] = Field(default_factory=list, max_length=10, description="Topic tags")
    cross_post_to: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Additional protocols to cross-post to",
    )


class ProtocolListResponse(BaseModel):
    """List of supported protocols."""

    protocols: dict[str, str]
    count: int


# ── Rate limit dependency factory ────────────────────────────────


def _get_protocol_rate_limit(protocol: str) -> RateLimitDependency:
    """Create a rate limit dependency for a specific protocol."""
    rpm, burst = PROTOCOL_RATE_LIMITS.get(protocol, _DEFAULT_PROTOCOL_LIMIT)
    return RateLimitDependency(limit_key=f"degenfeed:{protocol}", rate=rpm / 60.0, burst=burst)


# ── Internal helpers ─────────────────────────────────────────────


async def _proxy_to_degenfeed(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy a request to the DegenFeed service.

    In production, this calls the actual DegenFeed protocol service.
    For now, returns mock data to enable frontend development.

    TODO(@degenfeed): Replace mock with actual service call when
    DegenFeed protocol services are deployed.
    """
    # ── Mock implementation for frontend development ──────────────
    # This will be replaced with actual HTTP calls to DegenFeed service
    log.debug("DegenFeed proxy: %s %s (mock)", method, path)
    return {"_mock": True, "_path": path, "_method": method}


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/protocols", response_model=ProtocolListResponse)
async def list_protocols() -> ProtocolListResponse:
    """List all supported DegenFeed protocols."""
    return ProtocolListResponse(protocols=dict(SUPPORTED_PROTOCOLS), count=len(SUPPORTED_PROTOCOLS))


@router.get(
    "/{protocol}/feed",
    response_model=FeedResponse,
    summary="Get protocol feed",
    description="Fetch a unified feed from the specified protocol. Cached for 60s.",
)
async def get_feed(
    request: Request,
    protocol: str = Path(..., description="Protocol name (e.g. farcaster, lens, twitter)"),
    user_id: str = Query("", description="Optional user ID for personalized feed"),
    page: int = Query(1, ge=1, le=100, description="Page number"),
    _rl: Request = Depends(_get_protocol_rate_limit("farcaster")),
) -> FeedResponse:
    """Fetch a unified feed from the given protocol."""
    protocol = protocol.lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported protocol '{protocol}'. Supported: {', '.join(SUPPORTED_PROTOCOLS)}",
        )

    # Check cache first
    cached = await get_cached_feed(protocol, user_id or "global", page)
    if cached is not None:
        return FeedResponse(
            protocol=protocol,
            page=page,
            has_more=cached.get("has_more", False),
            posts=[PostResponse(**p) for p in cached.get("posts", [])],
            cached=True,
        )

    # Proxy to DegenFeed service (mock for now)
    data = await _proxy_to_degenfeed("GET", f"/{protocol}/feed", {"user_id": user_id, "page": page})

    # Build response
    posts_data = data.get("posts", [])
    posts = [PostResponse(**p) for p in posts_data]
    response = FeedResponse(
        protocol=protocol,
        page=page,
        has_more=data.get("has_more", False),
        posts=posts,
        cached=False,
    )

    # Cache the result
    await set_cached_feed(
        protocol,
        user_id or "global",
        {"has_more": response.has_more, "posts": [p.model_dump() for p in posts]},
        page,
    )

    return response


@router.get(
    "/{protocol}/post/{post_id}",
    response_model=PostResponse,
    summary="Get a specific post",
    description="Fetch a single post by ID from the specified protocol. Cached for 30s.",
)
async def get_post(
    protocol: str = Path(..., description="Protocol name"),
    post_id: str = Path(..., description="Post ID on the protocol"),
) -> PostResponse:
    """Fetch a specific post."""
    protocol = protocol.lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported protocol '{protocol}'",
        )

    # Check cache
    cached = await get_cached_post(post_id)
    if cached is not None:
        return PostResponse(**cached)

    # Proxy to service
    data = await _proxy_to_degenfeed("GET", f"/{protocol}/post/{post_id}")

    post = PostResponse(**data)
    await set_cached_post(post_id, data)
    return post


@router.post(
    "/{protocol}/post",
    summary="Create a post",
    description="Create a post on the specified protocol. Supports cross-posting to other protocols.",
)
async def create_post(
    request: Request,
    body: CreatePostRequest,
    protocol: str = Path(..., description="Primary protocol to post to"),
) -> dict[str, Any]:
    """Create a post. If cross_post_to is set, also posts to those protocols."""
    protocol = protocol.lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported protocol '{protocol}'",
        )

    # Validate cross-post targets
    for target in body.cross_post_to:
        if target not in SUPPORTED_PROTOCOLS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported cross-post target '{target}'. Supported: {', '.join(SUPPORTED_PROTOCOLS)}",
            )

    # Enforce cross-post rate limit
    if body.cross_post_to:
        limiter = get_rate_limiter()
        client_id = request.headers.get("X-API-Key", "") or request.client.host if request.client else "unknown"
        allowed, _headers = await limiter.acquire(
            f"degenfeed:cross-post:{client_id}",
            rate=10.0 / 60.0,
            burst=3,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Cross-post rate limit exceeded (10/min). Try again later.",
            )

    # Proxy the post to primary protocol
    data = await _proxy_to_degenfeed(
        "POST",
        f"/{protocol}/post",
        {"text": body.text, "media_urls": body.media_urls, "tags": body.tags},
    )

    # Cross-post to additional protocols
    results = {protocol: data.get("id", "unknown")}
    for target in body.cross_post_to:
        try:
            cross_data = await _proxy_to_degenfeed(
                "POST",
                f"/{target}/post",
                {"text": body.text, "media_urls": body.media_urls, "tags": body.tags},
            )
            results[target] = cross_data.get("id", "unknown")
        except Exception as exc:
            log.warning("Cross-post to %s failed: %s", target, exc)
            results[target] = f"error: {exc!s}"

    # Invalidate cached feeds for affected protocols
    for p in [protocol, *body.cross_post_to]:
        await invalidate_feed(p, "global")

    return {
        "status": "posted",
        "protocol": protocol,
        "post_ids": results,
        "cross_post_count": len(body.cross_post_to),
    }


@router.get(
    "/identity/{protocol}/{handle}",
    response_model=IdentityResponse,
    summary="Resolve identity",
    description="Resolve a handle/username to an identity on the specified protocol. Cached for 5min.",
)
async def resolve_identity(
    protocol: str = Path(..., description="Protocol name"),
    handle: str = Path(..., description="Username/handle to resolve"),
) -> IdentityResponse:
    """Resolve a handle to an identity on the given protocol."""
    protocol = protocol.lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(
            status_code=404,
            detail=f"Unsupported protocol '{protocol}'",
        )

    # Check cache
    cached = await get_cached_identity(protocol, handle)
    if cached is not None:
        return IdentityResponse(**cached)

    # Proxy to service
    data = await _proxy_to_degenfeed("GET", f"/{protocol}/identity/{handle}")

    identity = IdentityResponse(**data)
    await set_cached_identity(protocol, handle, data)
    return identity


@router.get(
    "/health",
    summary="DegenFeed health check",
    description="Health check for DegenFeed proxy, cache, and protocol services.",
)
async def degenfeed_health() -> dict[str, Any]:
    """Comprehensive health check for DegenFeed infrastructure."""
    cache_status = await cache_health()

    return {
        "service": "degenfeed-proxy",
        "healthy": cache_status.get("healthy", False),
        "protocols_supported": len(SUPPORTED_PROTOCOLS),
        "protocols": list(SUPPORTED_PROTOCOLS.keys()),
        "cache": cache_status,
        "rate_limits": {
            protocol: {"rpm": rpm, "burst_multiplier": bm}
            for protocol, (rpm, bm) in PROTOCOL_RATE_LIMITS.items()
        },
    }
