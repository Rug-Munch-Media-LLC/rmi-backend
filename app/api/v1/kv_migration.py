"""KV Migration API — self-hosted replacement for Cloudflare KV.

Phase 2 of the KV-to-selfhosted migration for the DegenFeed protocol.
Phase 1 (app/routers/degenfeed_storage.py) shipped the generic CACHE /
SUBSCRIPTIONS / BOOKMARKS / API_KEYS gateway. This router adds the
higher-level data the degenfeed-api worker previously stored in KV:

  Ephemeral (Redis):
    POST   /api/v1/rate-limits/report     — cross-isolate rate limit report (TTL = window)
    POST   /api/v1/rate-limits/batch      — batch rate limit sync from worker isolates
    GET    /api/v1/rate-limits/check      — rate limit status for an ip+path
    POST   /api/v1/queue/enqueue          — enqueue a sync|offline|premium|scheduled|bridgy:* job
    GET    /api/v1/queue/dequeue          — dequeue up to N jobs
    POST   /api/v1/sessions               — create/upsert a session (TTL)
    GET    /api/v1/sessions               — fetch a session by id
    DELETE /api/v1/sessions               — delete a session by id

  Persistent (Postgres via SQLAlchemy ORM — app/models/kv_migration.py):
    POST   /api/v1/chat-history           — store a chat message
    GET    /api/v1/chat-history           — list messages for a room
    POST   /api/v1/identities             — upsert an identity mapping (address → JSON)
    GET    /api/v1/identities             — fetch an identity mapping by address
    POST   /api/v1/bookmarks              — add a bookmark (idempotent)
    GET    /api/v1/bookmarks              — list bookmarks for an address

Auth: the router is registered under PUBLIC_PREFIXES (app/core/auth.py) and
enforces its own token check — accepting ``X-API-Key`` or
``Authorization: Bearer`` against RMI_AUTH_TOKEN or RMI_INTERNAL_KEY. This
mirrors the degenfeed_storage gateway so the worker can authenticate with
whichever shared secret it holds.

All responses are JSON. Writes return ``{"ok": true}``; reads return the
stored payload. Errors are RFC 7807 Problem Details (via HTTPException).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.dependencies import get_db
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["kv-migration"])

# ── Auth ──────────────────────────────────────────────────────────────

_AUTH_TOKENS = {
    token
    for token in (
        getattr(settings, "rmi_auth_token", None),
        getattr(settings, "rmi_internal_key", None),
    )
    if token
}


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
    """FastAPI dependency enforcing the KV migration auth check."""
    if not _authorized(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized — invalid X-API-Key",
        )


# ── Redis / Postgres helpers ──────────────────────────────────────────

# Key namespaces — kept distinct from Phase 1 (degenfeed_storage) keys.
_RL_KEY = "dfkv:rl:{ip}:{path}"  # rate limit record (count/max), TTL = window
_QUEUE_KEY = "dfkv:queue:{queue}"  # list of queued job JSON blobs
_SESSION_KEY = "dfkv:session:{session_id}"  # session JSON blob, TTL

_DEFAULT_SESSION_TTL = 86_400  # 24h — matches typical KV session expiry

_tables_ready = False


async def _redis() -> Any | None:
    """Async Redis client or None when Redis is unavailable."""
    try:
        from app.core.redis import get_redis_async

        return get_redis_async()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Redis unavailable for KV migration: %s", exc)
        return None


async def _ensure_kv_tables() -> None:
    """Create the KV-migration Postgres tables if missing (idempotent).

    Uses SQLAlchemy metadata create_all scoped to just our three tables —
    no-op when they already exist, no global schema drift.
    """
    global _tables_ready
    if _tables_ready:
        return
    try:
        from typing import cast

        from sqlalchemy import Table

        from app.core.database import engine
        from app.models import Base
        from app.models.kv_migration import Bookmark, ChatMessage, IdentityMapping

        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    cast("Table", IdentityMapping.__table__),
                    cast("Table", Bookmark.__table__),
                    cast("Table", ChatMessage.__table__),
                ],
            )
        _tables_ready = True
        log.info("kv_migration tables ready")
    except Exception as exc:  # pragma: no cover - defensive
        log.error("kv_migration tables create failed: %s", exc)
        raise HTTPException(status_code=503, detail="KV storage unavailable") from exc


def _key_escape(value: str) -> str:
    """Make a user-supplied value safe for use inside a Redis key.

    URL-percent-encodes the value so IPs/paths with separators cannot
    collide with our namespace structure (e.g. path containing ':').
    """
    return quote(value, safe="")


async def _store_rate_limit(
    redis: Any,
    ip: str,
    path: str,
    count: int,
    max_limit: int,
    window_ms: int,
) -> None:
    """Write a single rate limit record to Redis with TTL = window_ms.

    Extracted so both the single-report and batch-sync endpoints share
    the same storage logic.
    """
    key = _RL_KEY.format(ip=_key_escape(ip), path=_key_escape(path))
    ttl_secs = max(1, window_ms // 1000)
    await redis.set(key, json.dumps({"count": count, "max": max_limit}), ex=ttl_secs)


# ── Pydantic models ───────────────────────────────────────────────────


class RateLimitReport(BaseModel):
    """Cross-isolate rate limit report — an IP+path is near its limit."""

    ip: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1, max_length=512)
    count: int = Field(..., ge=0)
    max: int = Field(..., ge=1)
    window_ms: int = Field(..., ge=1, le=86_400_000)


class RateLimitStatus(BaseModel):
    """Current rate limit status for an ip+path."""

    count: int = 0
    blocked: bool = False


class RateLimitEntry(BaseModel):
    """A single rate limit entry in a batch sync payload."""

    ip: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1, max_length=512)
    count: int = Field(..., ge=0)
    max: int = Field(..., ge=1)
    window_ms: int = Field(5000, ge=1, le=86_400_000)


class RateLimitBatchRequest(BaseModel):
    """Batch sync payload from worker isolates."""

    source: str = Field("degenfeed-worker", min_length=1, max_length=128)
    entries: list[RateLimitEntry]


class BatchResponse(BaseModel):
    """Response for batch sync operations."""

    ok: bool = True
    accepted: int


class EnqueueRequest(BaseModel):
    """Body for POST /queue/enqueue."""

    queue: Literal["sync", "offline", "premium", "scheduled", "bridgy:users", "bridgy:config"]
    key: str = Field(..., min_length=1, max_length=512)
    payload: dict[str, Any] = Field(default_factory=dict)


class EnqueueResponse(BaseModel):
    """Acknowledged enqueue with the generated job id."""

    ok: bool
    id: str


class QueueItem(BaseModel):
    """A dequeued job."""

    id: str
    queue: str
    key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DequeueResponse(BaseModel):
    """Up to N dequeued jobs."""

    items: list[QueueItem] = Field(default_factory=list)


class SessionPutRequest(BaseModel):
    """Body for POST /sessions — create or upsert a session."""

    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Client-supplied session id; generated when omitted",
    )
    data: dict[str, Any] = Field(default_factory=dict)
    ttl: int = Field(
        _DEFAULT_SESSION_TTL,
        ge=1,
        le=2_592_000,
        description="TTL in seconds (1s - 30d)",
    )


class SessionResponse(BaseModel):
    """A stored session."""

    session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    ttl: int | None = None


class ChatMessageCreate(BaseModel):
    """Body for POST /chat-history."""

    room_id: str = Field(..., min_length=1, max_length=255)
    sender: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=100_000)


class ChatMessageOut(BaseModel):
    """A stored chat message."""

    id: str
    room_id: str
    sender: str
    content: str
    created_at: str


class ChatHistoryResponse(BaseModel):
    """List of chat messages for a room."""

    items: list[ChatMessageOut] = Field(default_factory=list)


class IdentityPut(BaseModel):
    """Body for POST /identities — upsert address → JSON blob."""

    address: str = Field(..., min_length=1, max_length=255)
    data: dict[str, Any] = Field(default_factory=dict)


class IdentityResponse(BaseModel):
    """An identity mapping."""

    address: str
    data: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None


class BookmarkCreate(BaseModel):
    """Body for POST /bookmarks."""

    address: str = Field(..., min_length=1, max_length=255)
    post_id: str = Field(..., min_length=1, max_length=255)


class BookmarkOut(BaseModel):
    """A stored bookmark."""

    id: str
    address: str
    post_id: str
    created_at: str


class BookmarkResponse(BaseModel):
    """List of bookmarks for an address."""

    items: list[BookmarkOut] = Field(default_factory=list)


class OkResponse(BaseModel):
    """Generic write acknowledgement."""

    ok: bool = True


def _iso(dt: datetime) -> str:
    """Serialize a datetime to ISO 8601 (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


# ── Rate limits (Redis) ───────────────────────────────────────────────


@router.post(
    "/rate-limits/report",
    response_model=OkResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def rate_limit_report(body: RateLimitReport) -> OkResponse:
    """Report an IP+path that is near its rate limit.

    Stores the count/max pair in Redis with a TTL equal to the reporting
    window, so cross-isolate enforcement can read it back on other edges.
    """
    redis = await _redis()
    if redis is None:
        # Graceful degradation — KV semantics: a missing report is harmless
        return OkResponse()
    try:
        await _store_rate_limit(redis, body.ip, body.path, body.count, body.max, body.window_ms)
        return OkResponse()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("rate_limit_report failed ip=%s path=%s err=%s", body.ip, body.path, exc)
        return OkResponse()


@router.post(
    "/rate-limits/batch",
    response_model=BatchResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def rate_limit_batch(body: RateLimitBatchRequest) -> BatchResponse:
    """Batch-sync rate limit records from worker isolates.

    Each entry is stored independently in Redis with the same TTL-based
    expiration as individual reports.  Failures for individual entries
    are logged but do not block the rest of the batch.
    """
    redis = await _redis()
    if redis is None:
        return BatchResponse(ok=True, accepted=0)
    accepted = 0
    for entry in body.entries:
        try:
            await _store_rate_limit(
                redis,
                entry.ip,
                entry.path,
                entry.count,
                entry.max,
                entry.window_ms,
            )
            accepted += 1
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "rate_limit_batch failed ip=%s path=%s err=%s",
                entry.ip,
                entry.path,
                exc,
            )
    return BatchResponse(ok=True, accepted=accepted)


@router.get(
    "/rate-limits/check",
    response_model=RateLimitStatus,
    dependencies=[Depends(_auth_dependency)],
)
async def rate_limit_check(
    ip: str = Query(..., min_length=1, max_length=64),
    path: str = Query(..., min_length=1, max_length=512),
) -> RateLimitStatus:
    """Return the current rate limit status for an ip+path."""
    redis = await _redis()
    if redis is None:
        return RateLimitStatus()
    key = _RL_KEY.format(ip=_key_escape(ip), path=_key_escape(path))
    try:
        raw = await redis.get(key)
        if not raw:
            return RateLimitStatus()
        value = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(value)
        count = int(data.get("count", 0))
        max_count = int(data.get("max", 0))
        return RateLimitStatus(count=count, blocked=max_count > 0 and count >= max_count)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("rate_limit_check failed key=%s err=%s", key, exc)
        return RateLimitStatus()


# ── Queue operations (Redis lists) ────────────────────────────────────


@router.post(
    "/queue/enqueue",
    response_model=EnqueueResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def queue_enqueue(body: EnqueueRequest) -> EnqueueResponse:
    """Enqueue a background job for sync, offline, or premium processing."""
    redis = await _redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Queue storage unavailable")
    job_id = str(uuid.uuid4())
    created_at = _iso(datetime.now(UTC))
    job = {
        "id": job_id,
        "queue": body.queue,
        "key": body.key,
        "payload": body.payload,
        "created_at": created_at,
    }
    key = _QUEUE_KEY.format(queue=body.queue)
    try:
        await redis.rpush(key, json.dumps(job))
        log.info("queue_enqueue queue=%s job_id=%s key=%s", body.queue, job_id, body.key)
        return EnqueueResponse(ok=True, id=job_id)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("queue_enqueue failed queue=%s err=%s", body.queue, exc)
        raise HTTPException(status_code=503, detail="Queue storage unavailable") from exc


@router.get(
    "/queue/dequeue",
    response_model=DequeueResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def queue_dequeue(
    queue: Literal["sync", "offline", "premium", "scheduled", "bridgy:users", "bridgy:config"] = Query(...),
    limit: int = Query(10, ge=1, le=100),
) -> DequeueResponse:
    """Dequeue up to N jobs from the front of a queue."""
    redis = await _redis()
    if redis is None:
        return DequeueResponse()
    key = _QUEUE_KEY.format(queue=queue)
    items: list[QueueItem] = []
    try:
        for _ in range(limit):
            raw = await redis.lpop(key)
            if raw is None:
                break
            value = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data = json.loads(value)
            items.append(
                QueueItem(
                    id=str(data.get("id", "")),
                    queue=str(data.get("queue", queue)),
                    key=str(data.get("key", "")),
                    payload=data.get("payload") or {},
                    created_at=str(data.get("created_at", "")),
                )
            )
        return DequeueResponse(items=items)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("queue_dequeue failed queue=%s err=%s", queue, exc)
        return DequeueResponse()


# ── Sessions (Redis, TTL) ─────────────────────────────────────────────


@router.post(
    "/sessions",
    response_model=SessionResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def session_upsert(body: SessionPutRequest) -> SessionResponse:
    """Create or upsert a session in Redis with a TTL."""
    redis = await _redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Session storage unavailable")
    session_id = body.session_id or str(uuid.uuid4())
    key = _SESSION_KEY.format(session_id=_key_escape(session_id))
    try:
        await redis.set(key, json.dumps(body.data), ex=body.ttl)
        return SessionResponse(session_id=session_id, data=body.data, ttl=body.ttl)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("session_upsert failed session_id=%s err=%s", session_id, exc)
        raise HTTPException(status_code=503, detail="Session storage unavailable") from exc


@router.get(
    "/sessions",
    response_model=SessionResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def session_get(
    session_id: str = Query(..., min_length=1, max_length=255),
) -> SessionResponse:
    """Fetch a session by id. Returns empty data when the session is unknown/expired."""
    redis = await _redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Session storage unavailable")
    key = _SESSION_KEY.format(session_id=_key_escape(session_id))
    try:
        raw = await redis.get(key)
        if not raw:
            return SessionResponse(session_id=session_id, data={})
        value = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(value)
        ttl = await redis.ttl(key)
        return SessionResponse(session_id=session_id, data=data, ttl=max(ttl, 0))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("session_get failed session_id=%s err=%s", session_id, exc)
        raise HTTPException(status_code=503, detail="Session storage unavailable") from exc


@router.delete(
    "/sessions",
    response_model=OkResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def session_delete(
    session_id: str = Query(..., min_length=1, max_length=255),
) -> OkResponse:
    """Delete a session by id."""
    redis = await _redis()
    if redis is None:
        return OkResponse()
    key = _SESSION_KEY.format(session_id=_key_escape(session_id))
    try:
        await redis.delete(key)
        return OkResponse()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("session_delete failed session_id=%s err=%s", session_id, exc)
        return OkResponse()


# ── Chat history (Postgres) ───────────────────────────────────────────


@router.post(
    "/chat-history",
    response_model=ChatMessageOut,
    dependencies=[Depends(_auth_dependency)],
)
async def chat_history_create(
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
) -> ChatMessageOut:
    """Store a chat message for a room."""
    from app.models.kv_migration import ChatMessage

    await _ensure_kv_tables()
    message = ChatMessage(room_id=body.room_id, sender=body.sender, content=body.content)
    try:
        db.add(message)
        await db.commit()
        await db.refresh(message)
        log.info("chat_message_created room_id=%s sender=%s", body.room_id, body.sender)
        return ChatMessageOut(
            id=str(message.id),
            room_id=message.room_id,
            sender=message.sender,
            content=message.content,
            created_at=_iso(message.created_at),
        )
    except Exception as exc:  # pragma: no cover - defensive
        await db.rollback()
        log.error("chat_history_create failed room_id=%s err=%s", body.room_id, exc)
        raise HTTPException(status_code=503, detail="Chat storage unavailable") from exc


@router.get(
    "/chat-history",
    response_model=ChatHistoryResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def chat_history_list(
    room_id: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ChatHistoryResponse:
    """List the most recent chat messages for a room (oldest first)."""
    from app.models.kv_migration import ChatMessage

    await _ensure_kv_tables()
    try:
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.room_id == room_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
        items = [
            ChatMessageOut(
                id=str(m.id),
                room_id=m.room_id,
                sender=m.sender,
                content=m.content,
                created_at=_iso(m.created_at),
            )
            for m in messages
        ]
        # Newest-first is convenient for feeds; return in display order (oldest first)
        items.reverse()
        return ChatHistoryResponse(items=items)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("chat_history_list failed room_id=%s err=%s", room_id, exc)
        raise HTTPException(status_code=503, detail="Chat storage unavailable") from exc


# ── Identities (Postgres) ─────────────────────────────────────────────


@router.post(
    "/identities",
    response_model=IdentityResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def identity_upsert(
    body: IdentityPut,
    db: AsyncSession = Depends(get_db),
) -> IdentityResponse:
    """Upsert an identity mapping (address → JSON blob)."""
    from app.models.kv_migration import IdentityMapping

    await _ensure_kv_tables()
    try:
        result = await db.execute(select(IdentityMapping).where(IdentityMapping.address == body.address))
        mapping = result.scalar_one_or_none()
        if mapping is None:
            mapping = IdentityMapping(address=body.address, data=body.data)
            db.add(mapping)
        else:
            mapping.data = body.data
        await db.commit()
        await db.refresh(mapping)
        log.info("identity_upsert address=%s", body.address)
        return IdentityResponse(
            address=mapping.address,
            data=mapping.data or {},
            updated_at=_iso(mapping.updated_at),
        )
    except Exception as exc:  # pragma: no cover - defensive
        await db.rollback()
        log.error("identity_upsert failed address=%s err=%s", body.address, exc)
        raise HTTPException(status_code=503, detail="Identity storage unavailable") from exc


@router.get(
    "/identities",
    response_model=IdentityResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def identity_get(
    address: str = Query(..., min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
) -> IdentityResponse:
    """Fetch an identity mapping by address."""
    from app.models.kv_migration import IdentityMapping

    await _ensure_kv_tables()
    try:
        result = await db.execute(select(IdentityMapping).where(IdentityMapping.address == address))
        mapping = result.scalar_one_or_none()
        if mapping is None:
            raise HTTPException(status_code=404, detail="Identity not found")
        return IdentityResponse(
            address=mapping.address,
            data=mapping.data or {},
            updated_at=_iso(mapping.updated_at),
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.error("identity_get failed address=%s err=%s", address, exc)
        raise HTTPException(status_code=503, detail="Identity storage unavailable") from exc


# ── Bookmarks (Postgres) ──────────────────────────────────────────────


@router.post(
    "/bookmarks",
    response_model=OkResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def bookmark_create(
    body: BookmarkCreate,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Add a bookmark for an address. Idempotent — duplicates are ignored."""
    from app.models.kv_migration import Bookmark

    await _ensure_kv_tables()
    try:
        existing = await db.execute(
            select(Bookmark).where(
                Bookmark.address == body.address,
                Bookmark.post_id == body.post_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(Bookmark(address=body.address, post_id=body.post_id))
            await db.commit()
            log.info("bookmark_created address=%s post_id=%s", body.address, body.post_id)
        return OkResponse()
    except Exception as exc:  # pragma: no cover - defensive
        await db.rollback()
        log.error("bookmark_create failed address=%s err=%s", body.address, exc)
        raise HTTPException(status_code=503, detail="Bookmark storage unavailable") from exc


@router.get(
    "/bookmarks",
    response_model=BookmarkResponse,
    dependencies=[Depends(_auth_dependency)],
)
async def bookmark_list(
    address: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> BookmarkResponse:
    """List bookmarks for an address (newest first)."""
    from app.models.kv_migration import Bookmark

    await _ensure_kv_tables()
    try:
        result = await db.execute(
            select(Bookmark).where(Bookmark.address == address).order_by(Bookmark.created_at.desc()).limit(limit)
        )
        bookmarks = result.scalars().all()
        return BookmarkResponse(
            items=[
                BookmarkOut(
                    id=str(b.id),
                    address=b.address,
                    post_id=b.post_id,
                    created_at=_iso(b.created_at),
                )
                for b in bookmarks
            ]
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.error("bookmark_list failed address=%s err=%s", address, exc)
        raise HTTPException(status_code=503, detail="Bookmark storage unavailable") from exc
