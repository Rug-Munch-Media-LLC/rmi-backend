"""Admin bulletin board endpoints + comment moderation (require admin session)."""

from __future__ import annotations

from fastapi import Body, Request

from app.bulletin_board import BulletinBoardManager, PostCategory, PostStatus
from app.core.errors import NotFoundError, ValidationError
from app.domains.admin.admin_backend import AuditLogger, require_admin
from app.routers.bulletin_board.models import (  # noqa: TC001 — Pydantic request models, needs runtime resolution
    CreatePostRequest,
    UpdatePostRequest,
)
from app.routers.bulletin_board.router import router


@router.get("/admin/bulletin/posts")
async def admin_list_posts(
    request: Request,
    category: str = "",
    status: str = "",
    search: str = "",
    pinned: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> dict:
    """List all posts (admin view with full data)."""
    auth = await require_admin(request, "content.read")
    auth["admin"]

    result = await BulletinBoardManager.list_posts(
        category=category or None,
        status=status or None,
        search_query=search or None,
        pinned_only=pinned if pinned is not None else False,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return {
        "posts": result["posts"],
        "total": result["total"],
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/admin/bulletin/posts",
    summary="Admin Create Post",
    description="Create a new bulletin board post. Supports Markdown content, categories, tags, meta description, scheduled publishing, and pinning.",
)
async def admin_create_post(request: Request, body: CreatePostRequest) -> dict[str, bool]:
    """Create a new bulletin board post."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    # Validate category
    if body.category not in [c.value for c in PostCategory]:
        raise ValidationError(message=f"Invalid category: {body.category}")

    # Validate status
    if body.status not in [s.value for s in PostStatus]:
        raise ValidationError(message=f"Invalid status: {body.status}")

    post = await BulletinBoardManager.create_post(
        title=body.title,
        content=body.content,
        category=body.category,
        author_id=admin["id"],
        author_email=admin["email"],
        author_name=admin.get("name", admin["email"].split("@")[0]),
        priority=body.priority,
        target_audience=body.target_audience,
        status=body.status,
        featured_image=body.featured_image,
        attachments=body.attachments,
        tags=body.tags,
        scheduled_at=body.scheduled_at,
        expires_at=body.expires_at,
        pinned=body.pinned,
        allow_comments=body.allow_comments,
        meta_title=body.meta_title,
        meta_description=body.meta_description,
        og_image=body.og_image,
    )

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.create",
        resource_type="post",
        resource_id=post.post_id,
        ip_address=ip,
        user_agent=ua,
        after_state={"title": post.title, "status": post.status, "category": post.category},
    )

    return {"success": True, "post": post.to_dict()}


@router.get("/admin/bulletin/posts/{post_id}")
async def admin_get_post(request: Request, post_id: str) -> dict[str, dict]:
    """Get full post details (admin)."""
    await require_admin(request, "content.read")

    post = await BulletinBoardManager.get_post(post_id)
    if not post:
        raise NotFoundError(message="Post not found")

    return {"post": post.to_dict()}


@router.put(
    "/admin/bulletin/posts/{post_id}",
    summary="Admin Update Post",
    description="Update an existing post. Supports partial updates: title, content, category, tags, meta description, and scheduled publish date.",
)
async def admin_update_post(
    request: Request,
    post_id: str,
    body: UpdatePostRequest,
) -> dict[str, bool]:
    """Update a post."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    # Build updates dict from non-None fields
    updates = {}
    for field_name, value in body.model_dump().items():
        if value is not None:
            updates[field_name] = value

    if not updates:
        raise ValidationError(message="No fields to update")

    updated = await BulletinBoardManager.update_post(
        post_id=post_id,
        updates=updates,
        editor_id=admin["id"],
        editor_email=admin["email"],
    )

    if not updated:
        raise NotFoundError(message="Post not found")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.update",
        resource_type="post",
        resource_id=post_id,
        ip_address=ip,
        user_agent=ua,
        after_state=updates,
    )

    return {"success": True, "post": updated.to_dict()}


@router.delete(
    "/admin/bulletin/posts/{post_id}",
    summary="Admin Delete Post",
    description="Soft-delete (archive) a post. Archived posts are hidden from public views but retained in the database for recovery.",
)
async def admin_delete_post(request: Request, post_id: str) -> dict[str, bool]:
    """Delete (archive) a post."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    result = await BulletinBoardManager.delete_post(post_id)
    if not result:
        raise NotFoundError(message="Post not found")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.delete",
        resource_type="post",
        resource_id=post_id,
        ip_address=ip,
        user_agent=ua,
    )

    return {"success": True, "message": "Post archived"}


@router.post(
    "/admin/bulletin/posts/{post_id}/publish",
    summary="Admin Publish Post",
    description="Immediately publish a draft or scheduled post. Sets the published_at timestamp and makes it visible to the public.",
)
async def admin_publish_post(request: Request, post_id: str) -> dict[str, bool]:
    """Publish a post immediately."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    updated = await BulletinBoardManager.update_post(
        post_id=post_id,
        updates={"status": "published"},
        editor_id=admin["id"],
        editor_email=admin["email"],
    )

    if not updated:
        raise NotFoundError(message="Post not found")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.publish",
        resource_type="post",
        resource_id=post_id,
        ip_address=ip,
        user_agent=ua,
    )

    return {"success": True, "published_at": updated.published_at}


@router.post(
    "/admin/bulletin/posts/{post_id}/unpublish",
    summary="Admin Unpublish Post",
    description="Unpublish a post and return it to draft status. The post becomes hidden from public views.",
)
async def admin_unpublish_post(request: Request, post_id: str) -> dict[str, bool]:
    """Unpublish a post (move to draft)."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    updated = await BulletinBoardManager.update_post(
        post_id=post_id,
        updates={"status": "draft"},
        editor_id=admin["id"],
        editor_email=admin["email"],
    )

    if not updated:
        raise NotFoundError(message="Post not found")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.unpublish",
        resource_type="post",
        resource_id=post_id,
        ip_address=ip,
        user_agent=ua,
    )

    return {"success": True}


@router.post(
    "/admin/bulletin/posts/{post_id}/pin",
    summary="Admin Pin Post",
    description="Toggle the pinned status of a post. Pinned posts appear at the top of bulletin listings.",
)
async def admin_pin_post(request: Request, post_id: str, body: dict = Body(...)) -> dict[str, bool]:
    """Pin or unpin a post."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    pinned = body.get("pinned", True)
    pin_order = body.get("pin_order", 0)

    updated = await BulletinBoardManager.update_post(
        post_id=post_id,
        updates={"pinned": pinned, "pin_order": pin_order},
        editor_id=admin["id"],
        editor_email=admin["email"],
    )

    if not updated:
        raise NotFoundError(message="Post not found")

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.post.pin" if pinned else "bulletin.post.unpin",
        resource_type="post",
        resource_id=post_id,
        ip_address=ip,
        user_agent=ua,
    )

    return {"success": True, "pinned": pinned}


@router.get("/admin/bulletin/stats")
async def admin_bulletin_stats(request: Request) -> dict:
    """Get bulletin board statistics."""
    await require_admin(request, "content.read")

    stats = await BulletinBoardManager.get_stats()
    return {"stats": stats}


@router.get("/admin/bulletin/scheduled")
async def admin_scheduled_posts(request: Request) -> dict:
    """List scheduled posts waiting to be published."""
    await require_admin(request, "content.read")

    result = await BulletinBoardManager.list_posts(
        status="scheduled",
        sort_by="scheduled_at",
        sort_order="asc",
    )
    return {"posts": result["posts"], "total": result["total"]}


@router.post("/admin/bulletin/process-scheduled")
async def admin_process_scheduled(request: Request) -> dict[str, bool]:
    """Process scheduled posts (publish ones that are due)."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    published = await BulletinBoardManager.publish_scheduled()

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.process_scheduled",
        resource_type="batch",
        resource_id="scheduled",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        after_state={"published_count": len(published)},
    )

    return {"success": True, "published": published, "count": len(published)}


@router.post("/admin/bulletin/process-expired")
async def admin_process_expired(request: Request) -> dict[str, bool]:
    """Process expired posts (archive ones past expiry)."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    archived = await BulletinBoardManager.archive_expired()

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="bulletin.process_expired",
        resource_type="batch",
        resource_id="expired",
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
        after_state={"archived_count": len(archived)},
    )

    return {"success": True, "archived": archived, "count": len(archived)}


# ── Comments (Admin Moderation) ─────────────────────────────


@router.get("/admin/bulletin/posts/{post_id}/comments")
async def admin_get_comments(request: Request, post_id: str) -> dict:
    """Get all comments for a post (admin moderation view)."""
    await require_admin(request, "content.read")

    comments = await BulletinBoardManager.get_comments(post_id)
    return {"comments": comments, "total": len(comments)}


@router.post("/admin/bulletin/comments/{comment_id}/moderate")
async def admin_moderate_comment(
    request: Request,
    comment_id: str,
    body: dict = Body(...),
) -> dict[str, bool]:
    """Moderate a comment (approve/reject)."""
    auth = await require_admin(request, "content.write")
    admin = auth["admin"]

    status = body.get("status", "approved")

    # TODO: Implement comment moderation in bulletin_board.py

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action=f"bulletin.comment.{status}",
        resource_type="comment",
        resource_id=comment_id,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )

    return {"success": True, "status": status}
