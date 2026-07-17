"""
RMI Bulletin Board API Router
===============================
Full REST API for bulletin board management.

Public endpoints (no auth):
  GET /api/v1/bulletin/posts          - List published posts
  GET /api/v1/bulletin/posts/{slug}  - Get single post by slug
  GET /api/v1/bulletin/categories    - List categories

Admin endpoints (require admin session):
  POST /api/v1/admin/bulletin/posts           - Create post
  PUT /api/v1/admin/bulletin/posts/{id}       - Update post
  DELETE /api/v1/admin/bulletin/posts/{id}    - Delete (archive) post
  GET /api/v1/admin/bulletin/posts            - List all posts (admin view)
  POST /api/v1/admin/bulletin/posts/{id}/publish   - Publish post
  POST /api/v1/admin/bulletin/posts/{id}/unpublish - Unpublish post
  POST /api/v1/admin/bulletin/posts/{id}/pin       - Pin/unpin post
  GET /api/v1/admin/bulletin/stats           - Board statistics
  GET /api/v1/admin/bulletin/scheduled       - List scheduled posts
  POST /api/v1/admin/bulletin/process-scheduled - Process scheduled posts
  POST /api/v1/admin/bulletin/process-expired - Archive expired posts
"""

from fastapi import APIRouter, Body, Request

from app.core.errors import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from pydantic import BaseModel, Field

from app.admin_backend import AuditLogger, require_admin
from app.bulletin_board import (
    BulletinBoardManager,
    PostCategory,
    PostStatus,
    award_badge,
)

router = APIRouter(prefix="/api/v1/bulletin-board", tags=["bulletin-board"])

# ── Public Endpoints ──────────────────────────────────────────


@router.get("/api/v1/bulletin/posts")
async def public_list_posts(
    request: Request,
    category: str = "",
    search: str = "",
    tag: str = "",
    limit: int = 20,
    offset: int = 0,
):
    """List published posts (public)."""
    tags = [tag] if tag else None
    result = await BulletinBoardManager.list_posts(
        category=category or None,
        status="published",
        search_query=search or None,
        tags=tags,
        limit=limit,
        offset=offset,
        sort_by="published_at",
        sort_order="desc",
    )

    # Return public-safe versions
    posts = []
    for p in result["posts"]:
        # Remove internal fields
        safe = {
            k: v
            for k, v in p.items()
            if k
            not in [
                "author_id",
                "author_email",
                "edit_history",
                "approved_by",
                "notification_sent",
                "version",
            ]
        }
        posts.append(safe)

    return {
        "posts": posts,
        "total": result["total"],
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/v1/bulletin/posts/{slug}")
async def public_get_post(request: Request, slug: str):
    """Get a single published post by slug (public)."""
    post = await BulletinBoardManager.get_post_by_slug(slug)
    if not post or post.status != PostStatus.PUBLISHED.value:
        raise NotFoundError(message="Post not found")

    # Track view
    await BulletinBoardManager.track_engagement(post.post_id, "view")

    return {"post": post.to_public_dict()}


@router.get("/api/v1/bulletin/categories")
async def public_categories():
    """List all categories with counts."""
    stats = await BulletinBoardManager.get_stats()
    categories = []
    for cat in PostCategory:
        categories.append(
            {
                "id": cat.value,
                "name": cat.value.replace("_", " ").title(),
                "count": stats["categories"].get(cat.value, 0),
            }
        )
    return {"categories": categories}


@router.get("/api/v1/bulletin/pinned")
async def public_pinned_posts(limit: int = 5):
    """Get pinned posts (public)."""
    result = await BulletinBoardManager.list_posts(
        pinned_only=True,
        status="published",
        limit=limit,
    )
    posts = []
    for p in result["posts"]:
        safe = {
            k: v
            for k, v in p.items()
            if k
            not in [
                "author_id",
                "author_email",
                "edit_history",
                "approved_by",
                "notification_sent",
                "version",
            ]
        }
        posts.append(safe)
    return {"posts": posts, "total": result["total"]}


# ── Admin Models ──────────────────────────────────────────────


class CreatePostRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str = "news"
    priority: str = "normal"
    target_audience: str = "all"
    status: str = "draft"
    featured_image: str = ""
    attachments: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    scheduled_at: str | None = None
    expires_at: str | None = None
    pinned: bool = False
    allow_comments: bool = False
    meta_title: str = ""
    meta_description: str = ""
    og_image: str = ""


class UpdatePostRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    category: str | None = None
    priority: str | None = None
    target_audience: str | None = None
    status: str | None = None
    featured_image: str | None = None
    attachments: list[dict] | None = None
    tags: list[str] | None = None
    scheduled_at: str | None = None
    expires_at: str | None = None
    pinned: bool | None = None
    allow_comments: bool | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    og_image: str | None = None


class CommentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    parent_id: str | None = None


# ── Admin Endpoints ───────────────────────────────────────────


@router.get("/api/v1/admin/bulletin/posts")
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
):
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


@router.post("/api/v1/admin/bulletin/posts")
async def admin_create_post(request: Request, body: CreatePostRequest):
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


@router.get("/api/v1/admin/bulletin/posts/{post_id}")
async def admin_get_post(request: Request, post_id: str):
    """Get full post details (admin)."""
    await require_admin(request, "content.read")

    post = await BulletinBoardManager.get_post(post_id)
    if not post:
        raise NotFoundError(message="Post not found")

    return {"post": post.to_dict()}


@router.put("/api/v1/admin/bulletin/posts/{post_id}")
async def admin_update_post(
    request: Request,
    post_id: str,
    body: UpdatePostRequest,
):
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


@router.delete("/api/v1/admin/bulletin/posts/{post_id}")
async def admin_delete_post(request: Request, post_id: str):
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


@router.post("/api/v1/admin/bulletin/posts/{post_id}/publish")
async def admin_publish_post(request: Request, post_id: str):
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


@router.post("/api/v1/admin/bulletin/posts/{post_id}/unpublish")
async def admin_unpublish_post(request: Request, post_id: str):
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


@router.post("/api/v1/admin/bulletin/posts/{post_id}/pin")
async def admin_pin_post(request: Request, post_id: str, body: dict = Body(...)):
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


@router.get("/api/v1/admin/bulletin/stats")
async def admin_bulletin_stats(request: Request):
    """Get bulletin board statistics."""
    await require_admin(request, "content.read")

    stats = await BulletinBoardManager.get_stats()
    return {"stats": stats}


@router.get("/api/v1/admin/bulletin/scheduled")
async def admin_scheduled_posts(request: Request):
    """List scheduled posts waiting to be published."""
    await require_admin(request, "content.read")

    result = await BulletinBoardManager.list_posts(
        status="scheduled",
        sort_by="scheduled_at",
        sort_order="asc",
    )
    return {"posts": result["posts"], "total": result["total"]}


@router.post("/api/v1/admin/bulletin/process-scheduled")
async def admin_process_scheduled(request: Request):
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


@router.post("/api/v1/admin/bulletin/process-expired")
async def admin_process_expired(request: Request):
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


@router.get("/api/v1/admin/bulletin/posts/{post_id}/comments")
async def admin_get_comments(request: Request, post_id: str):
    """Get all comments for a post (admin moderation view)."""
    await require_admin(request, "content.read")

    comments = await BulletinBoardManager.get_comments(post_id)
    return {"comments": comments, "total": len(comments)}


@router.post("/api/v1/admin/bulletin/comments/{comment_id}/moderate")
async def admin_moderate_comment(
    request: Request,
    comment_id: str,
    body: dict = Body(...),
):
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


# ═══════════════════════════════════════════════════
# BADGES & REPUTATION ENDPOINTS
# ═══════════════════════════════════════════════════


@router.get("/badges/{user_id}")
async def get_badges(user_id: str):
    """Get user badges."""
    from app.bulletin_board import get_user_badges, get_user_reputation

    badges = await get_user_badges(user_id)
    rep = await get_user_reputation(user_id)
    return {"user_id": user_id, "badges": badges, **rep}


@router.get("/leaderboard")
async def get_leaderboard(limit: int = 20):
    """Top users by reputation."""
    r = await BulletinBoardManager._get_redis()
    try:
        keys = await r.keys("bb:rep:*")
        users = []
        for k in keys[:50]:
            uid = k.split(":")[-1]
            rep = int(await r.get(k) or 0)
            bc = await r.scard(f"bb:badges:{uid}")
            if rep > 0:
                users.append({"user_id": uid, "reputation": rep, "badge_count": bc})
        users.sort(key=lambda u: u["reputation"], reverse=True)
        return {"leaderboard": users[:limit]}
    finally:
        await r.close()


# ═══════════════════════════════════════════════════
# X402 BOT POSTING - $1 per post for bots/agents
# ═══════════════════════════════════════════════════


@router.post("/x402/bot-post")
async def x402_bot_post(request: Request, data: dict):
    """
    Bot posting via x402 - $1 TO JOIN (one-time, 30-day membership).
    Pay $1 once, post unlimited for 30 days. Includes badges.
    """
    from app.bulletin_board import verify_x402_bot

    bot_addr = data.get("bot_address", "")
    tx_hash = data.get("tx_hash", "")
    title = data.get("title", "")
    content = data.get("content", "")
    category = data.get("category", "discussion")

    if not bot_addr or not tx_hash or not title or not content:
        raise ValidationError(message="Missing: bot_address, tx_hash, title, content")

    if not await verify_x402_bot(tx_hash, bot_addr):
        raise AuthorizationError(
            message="$1 to join - send USDC to x402 address, get tx_hash, post 30 days unlimited",
            status_code=402,
            code="payment.required",
        )

    post = await BulletinBoardManager.create_post(
        title=f"[BOT] {title}",
        content=content,
        author_id=f"bot:{bot_addr[:12]}",
        author_username=f"🤖 bot_{bot_addr[:8]}",
        category=category,
        is_bot=True,
        chain=data.get("chain", ""),
        token_address=data.get("token_address", ""),
        tags=data.get("tags", []),
        source="x402_api",
    )

    await award_badge(f"bot:{bot_addr[:12]}", "bot_verified")

    return {
        "success": True,
        "post": post.to_dict() if hasattr(post, "to_dict") else post,
        "bot_address": bot_addr,
        "membership": "30 days unlimited posting",
    }


@router.get("/x402/info")
async def x402_bot_info():
    """Info for bots wanting to join the Bulletin Board."""
    return {
        "service": "RMI Bulletin Board - x402 Bot Membership",
        "price": "$1.00 TO JOIN (30 days unlimited posting, one-time)",
        "payment": "Send $1 USDC to rugmunch.io/x402 payment address, receive tx_hash",
        "membership_includes": [
            "Unlimited posting in all 11 categories for 30 days",
            "Comment and vote on any post",
            "Earn badges: Verified Bot 🤖, Bot Pro ⚡, API Pioneer 🔌",
            "Your posts show 🤖 badge so humans know you're a bot",
            "Full x402 API access - automate everything",
        ],
        "categories": [
            "alert",
            "honeypot",
            "rugpull",
            "alpha",
            "whale",
            "bundler",
            "analysis",
            "discussion",
            "rehab",
            "debate",
        ],
        "join_endpoint": "POST /api/v1/bulletin-board/x402/bot-post",
        "required": ["bot_address", "tx_hash", "title", "content", "category"],
        "badges": ["bot_verified (on join)", "bot_pro (100+ posts)"],
    }
