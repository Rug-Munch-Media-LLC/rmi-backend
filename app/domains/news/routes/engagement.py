"""Comments + reactions endpoints for the T28 news domain.

Split out of ``app.domains.news/router.py`` (2026-08-14). Mounted under
``app.domains.news.router`` — do not mount directly.

Routes:
  POST   /api/v1/news/{news_id}/react        upsert/toggle like-dislike
  GET    /api/v1/news/{news_id}/reactions    counts + caller vote
  GET    /api/v1/news/{news_id}/comments     flat comment list (tombstones)
  POST   /api/v1/news/{news_id}/comments     create (optional parent_id)
  DELETE /api/v1/news/comment/{comment_id}   soft-delete (author/admin/moderator)
  POST   /api/v1/news/comment/{comment_id}/react  comment reaction toggle

Tables: news_reactions, news_comments, news_comment_reactions (see
app/catalog/init_schema.py + alembic c1d2e3f4a5b6).

Auth binding: the news domain has no auth of its own. Writes bind to the
unified session identity — the per-user JWT in the ``rmi_token`` cookie or
``Authorization: Bearer`` validated by app.auth.jwt_validation (the same
Tier-2 identity the auth middleware attaches to request.state.user). Reads
are public; anonymous callers get guest=True and my_* fields stay None.
The optional ``wallet`` claim is the wallet fallback identity.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.catalog.service import get_catalog
from app.core.rate_limiter import RateLimitDependency
from app.domains.news.routes.schemas import (
    CommentCreate,
    CommentListResponse,
    CommentOut,
    CommentReactionResponse,
    ReactionRequest,
    ReactionsSummary,
    _coerce_reaction,
    _sanitize_comment_body,
)

router = APIRouter(tags=["news"])


async def _session_user(request: Request) -> dict[str, Any] | None:
    """Unified session user claims (JWT cookie / Bearer, Tier-2 identity)."""
    from app.auth.jwt_validation import get_current_user

    return await get_current_user(request)


async def _resolve_identity(request: Request) -> tuple[str | None, bool]:
    """Return (user_id, guest). Falls back to the wallet claim, else anon."""
    user = await _session_user(request)
    if not user:
        return None, True
    user_id = str(user.get("id") or user.get("sub") or "").strip() or None
    if user_id:
        return user_id, False
    wallet = str(user.get("wallet") or "").strip() or None
    if wallet:
        return wallet, False
    return None, True


async def _reaction_counts(catalog: Any, news_id: str) -> tuple[int, int]:
    """(like_count, dislike_count) for a news item."""
    async with catalog._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_reactions WHERE news_id = $1",
            news_id,
        )
    return ((row["like_count"] if row else 0), (row["dislike_count"] if row else 0))


# ── POST /api/v1/news/{news_id}/react ─────────────────────────────
@router.post("/news/{news_id}/react", response_model=ReactionsSummary)
async def react_to_news(news_id: str, req: ReactionRequest, request: Request) -> ReactionsSummary:
    """Upsert a like/dislike vote. Sending the same reaction again toggles it off."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to react")
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    toggled_off = False
    async with catalog._pg_pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT reaction FROM news_reactions WHERE news_id=$1 AND user_id=$2",
            news_id,
            user_id,
        )
        if existing and existing["reaction"] == req.reaction:
            toggled_off = True
            await conn.execute(
                "DELETE FROM news_reactions WHERE news_id=$1 AND user_id=$2",
                news_id,
                user_id,
            )
        else:
            await conn.execute(
                "INSERT INTO news_reactions (news_id, user_id, reaction, created_at) "
                "VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (news_id, user_id) DO UPDATE SET reaction = EXCLUDED.reaction, created_at = NOW()",
                news_id,
                user_id,
                req.reaction,
            )
    like_count, dislike_count = await _reaction_counts(catalog, news_id)
    return ReactionsSummary(
        news_id=news_id,
        like_count=like_count,
        dislike_count=dislike_count,
        my_reaction=None if toggled_off else req.reaction,
    )


# ── GET /api/v1/news/{news_id}/reactions ──────────────────────────
@router.get("/news/{news_id}/reactions", response_model=ReactionsSummary)
async def get_news_reactions(news_id: str, request: Request) -> ReactionsSummary:
    """Reaction counts + the caller's vote (null when anonymous or no vote)."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    like_count, dislike_count = await _reaction_counts(catalog, news_id)
    user_id, _ = await _resolve_identity(request)
    my_reaction: Literal["like", "dislike"] | None = None
    if user_id:
        async with catalog._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reaction FROM news_reactions WHERE news_id=$1 AND user_id=$2",
                news_id,
                user_id,
            )
        my_reaction = _coerce_reaction(row["reaction"] if row else None)
    return ReactionsSummary(
        news_id=news_id,
        like_count=like_count,
        dislike_count=dislike_count,
        my_reaction=my_reaction,
    )


# ── GET /api/v1/news/{news_id}/comments ───────────────────────────
@router.get("/news/{news_id}/comments", response_model=CommentListResponse)
async def list_comments(news_id: str, request: Request) -> CommentListResponse:
    """List comments for an article. Flat list; threads assemble client-side
    via parent_id. Deleted comments return as tombstones (body=None, deleted=True)."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    user_id, _ = await _resolve_identity(request)
    async with catalog._pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.id, c.news_id, c.user_id, c.parent_id, c.body, c.created_at, "
            "c.edited_at, c.deleted_at, "
            "COALESCE(r.like_count, 0)::int AS like_count, "
            "COALESCE(r.dislike_count, 0)::int AS dislike_count "
            "FROM news_comments c "
            "LEFT JOIN (SELECT comment_id, "
            "COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_comment_reactions GROUP BY comment_id) r ON r.comment_id = c.id "
            "WHERE c.news_id = $1 "
            "ORDER BY c.created_at ASC, c.id ASC",
            news_id,
        )
        my_rows: dict[str, str] = {}
        if user_id and rows:
            my_rows = {
                str(row["comment_id"]): row["reaction"]
                for row in await conn.fetch(
                    "SELECT comment_id, reaction FROM news_comment_reactions "
                    "WHERE user_id = $1 AND comment_id = ANY($2::uuid[])",
                    user_id,
                    [r["id"] for r in rows],
                )
            }
    items = []
    for r in rows:
        is_deleted = r["deleted_at"] is not None
        items.append(
            CommentOut(
                id=str(r["id"]),
                news_id=r["news_id"],
                user_id=r["user_id"],
                parent_id=str(r["parent_id"]) if r["parent_id"] else None,
                body=None if is_deleted else r["body"],
                created_at=r["created_at"],
                edited_at=r["edited_at"],
                deleted=is_deleted,
                like_count=r["like_count"],
                dislike_count=r["dislike_count"],
                my_reaction=_coerce_reaction(my_rows.get(str(r["id"]))),
                is_mine=(user_id is not None and r["user_id"] == user_id),
            )
        )
    return CommentListResponse(items=items, total=len(items))


# ── POST /api/v1/news/{news_id}/comments ──────────────────────────
@router.post(
    "/news/{news_id}/comments",
    response_model=CommentOut,
    dependencies=[Depends(RateLimitDependency(limit_key="news_comment_create", rate=5.0 / 60.0, burst=5))],
)
async def create_comment(news_id: str, req: CommentCreate, request: Request) -> CommentOut:
    """Create a comment (optionally threaded via parent_id). Auth required."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to comment")
    body = _sanitize_comment_body(req.body)
    if not body:
        raise HTTPException(422, "comment body empty after sanitization")
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    parent_id: UUID | None = None
    async with catalog._pg_pool.acquire() as conn:
        # Article must exist (news_items or legacy crypto_news).
        exists = await conn.fetchrow("SELECT 1 FROM news_items WHERE news_id=$1", news_id)
        if not exists:
            exists = await conn.fetchrow("SELECT 1 FROM crypto_news WHERE id=$1", news_id)
        if not exists:
            raise HTTPException(404, "news item not found")
        if req.parent_id:
            try:
                parent_id = UUID(req.parent_id)
            except ValueError:
                raise HTTPException(422, "invalid parent_id") from None
            parent = await conn.fetchrow(
                "SELECT 1 FROM news_comments WHERE id=$1 AND news_id=$2",
                parent_id,
                news_id,
            )
            if not parent:
                raise HTTPException(404, "parent comment not found")
        row = await conn.fetchrow(
            "INSERT INTO news_comments (news_id, user_id, parent_id, body, created_at) "
            "VALUES ($1, $2, $3, $4, NOW()) "
            "RETURNING id, news_id, user_id, parent_id, body, created_at, edited_at, deleted_at",
            news_id,
            user_id,
            parent_id,
            body,
        )
    return CommentOut(
        id=str(row["id"]),
        news_id=row["news_id"],
        user_id=row["user_id"],
        parent_id=str(row["parent_id"]) if row["parent_id"] else None,
        body=row["body"],
        created_at=row["created_at"],
        edited_at=row["edited_at"],
        deleted=False,
        like_count=0,
        dislike_count=0,
        my_reaction=None,
        is_mine=True,
    )


# ── DELETE /api/v1/news/comment/{comment_id} ──────────────────────
@router.delete("/news/comment/{comment_id}")
async def delete_comment(comment_id: str, request: Request) -> dict[str, bool]:
    """Soft-delete a comment (tombstone). Author, admin, or moderator only."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required")
    try:
        cid = UUID(comment_id)
    except ValueError:
        raise HTTPException(422, "invalid comment id") from None
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    async with catalog._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, deleted_at FROM news_comments WHERE id=$1",
            cid,
        )
        if not row or row["deleted_at"] is not None:
            raise HTTPException(404, "comment not found")
        user = await _session_user(request)
        role = str((user or {}).get("role") or "USER").upper()
        if row["user_id"] != user_id and role not in ("ADMIN", "MODERATOR"):
            raise HTTPException(403, "not authorized to delete this comment")
        await conn.execute("UPDATE news_comments SET deleted_at = NOW() WHERE id=$1", cid)
    return {"ok": True}


# ── POST /api/v1/news/comment/{comment_id}/react ──────────────────
@router.post("/news/comment/{comment_id}/react", response_model=CommentReactionResponse)
async def react_to_comment(comment_id: str, req: ReactionRequest, request: Request) -> CommentReactionResponse:
    """Upsert a like/dislike on a comment; same reaction again toggles it off."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to react")
    try:
        cid = UUID(comment_id)
    except ValueError:
        raise HTTPException(422, "invalid comment id") from None
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    my_reaction: Literal["like", "dislike"] | None = None
    async with catalog._pg_pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM news_comments WHERE id=$1", cid)
        if not exists:
            raise HTTPException(404, "comment not found")
        existing = await conn.fetchrow(
            "SELECT reaction FROM news_comment_reactions WHERE comment_id=$1 AND user_id=$2",
            cid,
            user_id,
        )
        if existing and existing["reaction"] == req.reaction:
            await conn.execute(
                "DELETE FROM news_comment_reactions WHERE comment_id=$1 AND user_id=$2",
                cid,
                user_id,
            )
        else:
            await conn.execute(
                "INSERT INTO news_comment_reactions (comment_id, user_id, reaction, created_at) "
                "VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (comment_id, user_id) DO UPDATE SET reaction = EXCLUDED.reaction, created_at = NOW()",
                cid,
                user_id,
                req.reaction,
            )
            my_reaction = req.reaction
        counts = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_comment_reactions WHERE comment_id = $1",
            cid,
        )
    return CommentReactionResponse(
        comment_id=str(cid),
        like_count=counts["like_count"] if counts else 0,
        dislike_count=counts["dislike_count"] if counts else 0,
        my_reaction=my_reaction,
    )


__all__ = [
    "create_comment",
    "delete_comment",
    "get_news_reactions",
    "list_comments",
    "react_to_comment",
    "react_to_news",
    "router",
]
