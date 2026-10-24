"""Comment routes: create, threaded pagination, edit, soft delete, report, moderate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.rate_limiter import RateLimitDependency
from app.domains.engagement.deps import (
    BanPolicy,
    get_ban_policy,
    get_optional_actor,
    get_store,
    require_actor,
)
from app.domains.engagement.permissions import Actor, can_delete, can_edit, can_moderate, next_status
from app.domains.engagement.schemas import (
    CommentCreate,
    CommentListResponse,
    CommentOut,
    CommentQuery,
    CommentUpdate,
    ReportRequest,
    ReportResponse,
    StatusUpdateRequest,
    sanitize_body,
)
from app.domains.engagement.service import build_thread, comment_state, to_comment_out

if TYPE_CHECKING:
    from app.domains.engagement.models import ArticleComment
    from app.domains.engagement.port import EngagementStore

router = APIRouter(tags=["engagement"])

AUTO_HIDE_AT = 3
_PAGE_LIMIT_MAX = 100


async def _require_article(store: EngagementStore, article_id: str) -> None:
    if not await store.article_exists(article_id):
        raise HTTPException(status_code=404, detail="article not found")


async def _load_comment(store: EngagementStore, article_id: str, comment_id: str) -> ArticleComment:
    comment = await store.get_comment(article_id, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="comment not found")
    return comment


async def _resolve_parent(store: EngagementStore, article_id: str, parent_id: str | None) -> str | None:
    """Depth is one: replying to a reply attaches to that reply's root."""
    if not parent_id:
        return None
    parent = await store.get_comment(article_id, parent_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="parent comment not found")
    return parent.parent_id or parent.id


def _clean_body(raw: str) -> str:
    body = sanitize_body(raw)
    if not body:
        raise HTTPException(status_code=422, detail="comment body is empty after sanitisation")
    return body


@router.post(
    "/{article_id}/comments",
    response_model=CommentOut,
    status_code=201,
    dependencies=[Depends(RateLimitDependency(limit_key="article_comment_create", rate=0.0833, burst=5))],
)
async def create_comment(
    article_id: str,
    payload: CommentCreate,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
    policy: BanPolicy = Depends(get_ban_policy),
) -> CommentOut:
    """Create a comment or a depth-1 reply. The author is never spoofable."""
    if await policy.is_banned(actor.user_id):
        raise HTTPException(status_code=403, detail="banned accounts cannot post")
    await _require_article(store, article_id)
    parent_id = await _resolve_parent(store, article_id, payload.parent_id)
    body = _clean_body(payload.body)
    comment = await store.create_comment(article_id=article_id, user_id=actor.user_id, body=body, parent_id=parent_id)
    return to_comment_out(comment, actor)


@router.get("/{article_id}/comments", response_model=CommentListResponse)
async def list_comments(
    article_id: str,
    query: Annotated[CommentQuery, Query()],
    actor: Actor | None = Depends(get_optional_actor),
    store: EngagementStore = Depends(get_store),
) -> CommentListResponse:
    """One page of root comments, each with its replies attached."""
    await _require_article(store, article_id)
    roots, total = await store.list_roots(
        article_id, limit=query.limit, offset=query.offset, newest_first=query.sort == "newest"
    )
    replies = await store.list_replies([root.id for root in roots])
    return CommentListResponse(
        items=build_thread(roots, replies, actor),
        total=total,
        limit=query.limit,
        offset=query.offset,
        has_more=query.offset + len(roots) < total,
    )


@router.patch("/{article_id}/comments/{comment_id}", response_model=CommentOut)
async def edit_comment(
    article_id: str,
    comment_id: str,
    payload: CommentUpdate,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> CommentOut:
    """Authors may edit their own live comment; everyone else is refused."""
    comment = await _load_comment(store, article_id, comment_id)
    if not can_edit(actor, comment_state(comment)):
        raise HTTPException(status_code=403, detail="not allowed to edit this comment")
    updated = await store.update_comment(comment, {"body": _clean_body(payload.body), "edited_at": datetime.now(UTC)})
    return to_comment_out(updated, actor)


@router.delete("/{article_id}/comments/{comment_id}", response_model=CommentOut)
async def delete_comment(
    article_id: str,
    comment_id: str,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> CommentOut:
    """Soft delete (tombstone). Repeating it is a no-op, not an error."""
    comment = await _load_comment(store, article_id, comment_id)
    state = comment_state(comment)
    if state.deleted:
        return to_comment_out(comment, actor)
    if not can_delete(actor, state):
        raise HTTPException(status_code=403, detail="not allowed to delete this comment")
    updated = await store.update_comment(comment, {"deleted_at": datetime.now(UTC)})
    return to_comment_out(updated, actor)


@router.post(
    "/{article_id}/comments/{comment_id}/report",
    response_model=ReportResponse,
    dependencies=[Depends(RateLimitDependency(limit_key="article_comment_report", rate=0.1667, burst=10))],
)
async def report_comment(
    article_id: str,
    comment_id: str,
    payload: ReportRequest,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> ReportResponse:
    """One report per user. The nth report auto-hides the comment."""
    comment = await _load_comment(store, article_id, comment_id)
    if comment.user_id == actor.user_id:
        raise HTTPException(status_code=422, detail="cannot report your own comment")
    reporters = list(comment.reported_by or [])
    if actor.user_id not in reporters:
        reporters.append(actor.user_id)
    fields: dict[str, object] = {"reported_by": reporters, "report_count": len(reporters)}
    if len(reporters) >= AUTO_HIDE_AT and comment.status == "visible":
        fields["status"] = "hidden"
    updated = await store.update_comment(comment, fields)
    return ReportResponse(status=updated.status, report_count=updated.report_count)


@router.patch("/{article_id}/comments/{comment_id}/status", response_model=CommentOut)
async def set_comment_status(
    article_id: str,
    comment_id: str,
    payload: StatusUpdateRequest,
    actor: Actor = Depends(require_actor),
    store: EngagementStore = Depends(get_store),
) -> CommentOut:
    """Moderator-only status change. Illegal transitions are 422, not silent."""
    comment = await _load_comment(store, article_id, comment_id)
    if not can_moderate(actor):
        raise HTTPException(status_code=403, detail="moderator role required")
    try:
        status = next_status(comment.status, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = await store.update_comment(comment, {"status": status})
    return to_comment_out(updated, actor)
