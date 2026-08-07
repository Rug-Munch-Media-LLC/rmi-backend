"""DegenFeed KV Storage Gateway — Redis + Postgres backed KV for the DegenFeed worker.

Phase 1 of the KV-to-selfhosted migration. The DegenFeed Cloudflare Worker
cannot open TCP sockets to rmi-redis / rmi-postgres (localhost-only, no
public tunnels), so it reaches this gateway over HTTPS (public URL) instead.

Namespace mapping (matches the worker's KV bindings):

  /cache/{key}                    -> Redis GET / SETEX / DEL        (CACHE)
  /kv/subscriptions/{key}         -> Postgres degenfeed_kv          (SUBSCRIPTIONS)
  /kv/bookmarks/{key}             -> Postgres degenfeed_kv          (BOOKMARKS)
  /kv/api_keys/{key}              -> Postgres degenfeed_kv          (API_KEYS)
                                     + read-through Redis cache     (hot path)

Auth: the router is registered under PUBLIC_PREFIXES (app/core/auth.py) and
enforces its own token check, accepting `X-API-Key` or `Authorization: Bearer`
against RMI_AUTH_TOKEN or RMI_INTERNAL_KEY. This mirrors the x402 middleware
pattern so the worker can authenticate with whichever secret it holds.

All responses are JSON: { "value": string | null } for GET, { "ok": true } for
PUT/DELETE. Errors are RFC 7807 Problem Details.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/degenfeed/storage", tags=["degenfeed-storage"])

# ── Auth ──────────────────────────────────────────────────────────────

_AUTH_TOKENS = {
    token
    for token in (
        getattr(settings, "rmi_auth_token", None),
        getattr(settings, "rmi_internal_key", None),
    )
    if token
}

# API_KEYS namespace uses read-through Redis caching (hot path).
_API_KEYS_NAMESPACE = "api_keys"
_API_KEYS_CACHE_TTL = 300  # 5 min — short enough to revoke quickly


def _authorized(request: Request) -> bool:
    """Accept X-API-Key or Authorization: Bearer against either RMI token."""
    token = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return False
    if token in _AUTH_TOKENS:
        return True
    # Dev bypass — explicitly opt-in, never in prod
    return bool(getattr(settings, "dev_bypass_auth", False))


def _auth_dependency(request: Request) -> None:
    """FastAPI dependency enforcing the gateway auth check."""
    if not _authorized(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized — invalid X-API-Key",
        )


# ── Postgres KV table (lazy create once per process) ────────────────

_kv_table_ready = False

_CREATE_KV_TABLE = """
CREATE TABLE IF NOT EXISTS degenfeed_kv (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
"""


async def _ensure_kv_table() -> None:
    """Create the degenfeed_kv table if it does not exist (idempotent)."""
    global _kv_table_ready
    if _kv_table_ready:
        return
    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.begin() as conn:
            await conn.execute(text(_CREATE_KV_TABLE))
        _kv_table_ready = True
        log.info("degenfeed_kv table ready")
    except Exception as exc:  # pragma: no cover - defensive
        log.error("degenfeed_kv table create failed: %s", exc)
        raise HTTPException(status_code=503, detail="KV storage unavailable")


async def _redis() -> Any | None:
    """Async Redis client or None when Redis is unavailable."""
    try:
        from app.core.redis import get_redis_async

        return get_redis_async()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Redis unavailable for degenfeed storage: %s", exc)
        return None


# ── Models ───────────────────────────────────────────────────────────


class CachePutRequest(BaseModel):
    """Body for PUT /cache/{key} — Redis SETEX."""

    value: str = Field(..., description="Raw string value to store")
    ttl: int = Field(300, ge=1, le=2_592_000, description="TTL in seconds (1s - 30d)")


class KvPutRequest(BaseModel):
    """Body for PUT /kv/{namespace}/{key} — Postgres upsert."""

    value: str = Field(..., description="Raw string value to store")


# ── CACHE endpoints (Redis) ─────────────────────────────────────────


@router.get("/cache/{key:path}", dependencies=[Depends(_auth_dependency)])
async def cache_get(key: str) -> dict[str, str | None]:
    """Redis GET — return { value: string | null }."""
    redis = await _redis()
    if redis is None:
        return {"value": None}
    try:
        raw = await redis.get(key)
        return {"value": raw.decode("utf-8") if isinstance(raw, bytes) else raw}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("degenfeed cache GET failed key=%s err=%s", key, exc)
        return {"value": None}


@router.put("/cache/{key:path}", dependencies=[Depends(_auth_dependency)])
async def cache_put(key: str, body: CachePutRequest) -> dict[str, bool]:
    """Redis SETEX — store value with TTL."""
    redis = await _redis()
    if redis is None:
        return {"ok": False}
    try:
        await redis.set(key, body.value, ex=body.ttl)
        return {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("degenfeed cache PUT failed key=%s err=%s", key, exc)
        return {"ok": False}


@router.delete("/cache/{key:path}", dependencies=[Depends(_auth_dependency)])
async def cache_delete(key: str) -> dict[str, bool]:
    """Redis DEL."""
    redis = await _redis()
    if redis is None:
        return {"ok": False}
    try:
        await redis.delete(key)
        return {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("degenfeed cache DEL failed key=%s err=%s", key, exc)
        return {"ok": False}


# ── KV endpoints (Postgres) ─────────────────────────────────────────


@router.get("/kv/{namespace}/{key:path}", dependencies=[Depends(_auth_dependency)])
async def kv_get(namespace: str, key: str) -> dict[str, str | None]:
    """Postgres read. For api_keys, does a read-through Redis cache first (hot path)."""
    if namespace not in {"subscriptions", "bookmarks", "api_keys"}:
        raise HTTPException(status_code=400, detail=f"Unknown namespace '{namespace}'")

    await _ensure_kv_table()

    # api_keys hot path: read-through Redis cache
    if namespace == _API_KEYS_NAMESPACE:
        redis = await _redis()
        if redis is not None:
            try:
                cached = await redis.get(f"df:{namespace}:{key}")
                if cached is not None:
                    val = cached.decode("utf-8") if isinstance(cached, bytes) else cached
                    return {"value": val}
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("api_keys cache read miss key=%s err=%s", key, exc)

    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT value FROM degenfeed_kv WHERE namespace = :ns AND key = :key"),
                {"ns": namespace, "key": key},
            )
            row = result.first()
            value = row[0] if row else None

        if value is not None and namespace == _API_KEYS_NAMESPACE:
            redis = await _redis()
            if redis is not None:
                with contextlib.suppress(Exception):  # pragma: no cover - defensive
                    await redis.set(f"df:{namespace}:{key}", value, ex=_API_KEYS_CACHE_TTL)

        return {"value": value}
    except Exception as exc:  # pragma: no cover - defensive
        log.error("degenfeed kv GET failed ns=%s key=%s err=%s", namespace, key, exc)
        raise HTTPException(status_code=503, detail="KV storage unavailable")


@router.put("/kv/{namespace}/{key:path}", dependencies=[Depends(_auth_dependency)])
async def kv_put(namespace: str, key: str, body: KvPutRequest) -> dict[str, bool]:
    """Postgres upsert. Invalidates the Redis cache for api_keys."""
    if namespace not in {"subscriptions", "bookmarks", "api_keys"}:
        raise HTTPException(status_code=400, detail=f"Unknown namespace '{namespace}'")

    await _ensure_kv_table()

    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO degenfeed_kv (namespace, key, value, updated_at)
                    VALUES (:ns, :key, :value, now())
                    ON CONFLICT (namespace, key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """
                ),
                {"ns": namespace, "key": key, "value": body.value},
            )

        if namespace == _API_KEYS_NAMESPACE:
            redis = await _redis()
            if redis is not None:
                with contextlib.suppress(Exception):  # pragma: no cover - defensive
                    await redis.set(f"df:{namespace}:{key}", body.value, ex=_API_KEYS_CACHE_TTL)

        return {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive
        log.error("degenfeed kv PUT failed ns=%s key=%s err=%s", namespace, key, exc)
        return {"ok": False}


@router.delete("/kv/{namespace}/{key:path}", dependencies=[Depends(_auth_dependency)])
async def kv_delete(namespace: str, key: str) -> dict[str, bool]:
    """Postgres delete. Invalidates the Redis cache for api_keys."""
    if namespace not in {"subscriptions", "bookmarks", "api_keys"}:
        raise HTTPException(status_code=400, detail=f"Unknown namespace '{namespace}'")

    await _ensure_kv_table()

    try:
        from sqlalchemy import text

        from app.core.database import engine

        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM degenfeed_kv WHERE namespace = :ns AND key = :key"),
                {"ns": namespace, "key": key},
            )

        if namespace == _API_KEYS_NAMESPACE:
            redis = await _redis()
            if redis is not None:
                with contextlib.suppress(Exception):  # pragma: no cover - defensive
                    await redis.delete(f"df:{namespace}:{key}")

        return {"ok": True}
    except Exception as exc:  # pragma: no cover - defensive
        log.error("degenfeed kv DEL failed ns=%s key=%s err=%s", namespace, key, exc)
        return {"ok": False}
