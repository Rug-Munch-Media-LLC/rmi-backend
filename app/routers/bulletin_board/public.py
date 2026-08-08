"""Public bulletin board endpoints (no auth required)."""

from __future__ import annotations

from fastapi import Request  # noqa: TC002 — FastAPI needs runtime resolution for request objects

from app.bulletin_board import BulletinBoardManager, PostCategory, PostStatus
from app.core.errors import NotFoundError
from app.routers.bulletin_board.models import PublicPostRequest  # noqa: TC001 — Pydantic request model
from app.routers.bulletin_board.router import router


@router.post(
    "/posts/public",
    summary="Public Create Post",
    description="Create a public discussion post without authentication. For community token discussions. Posts are published immediately with community category.",
)
async def public_create_post(body: PublicPostRequest) -> dict:
    """Create a public discussion post (no auth required)."""
    import time

    post = await BulletinBoardManager.create_post(
        title=body.title or f"Discussion: {time.strftime('%b %d, %Y')}",
        content=body.content,
        category="community",
        author_id=f"anon_{int(time.time())}",
        author_email="anon@rugmunch.io",
        author_name=body.author_name or "Community Member",
        status="published",
        tags=body.tags,
        priority="normal",
        target_audience="all",
        allow_comments=True,
    )

    return {
        "success": True,
        "post_id": post.post_id,
        "slug": post.slug,
        "message": "Discussion post created successfully",
    }


@router.get(
    "/posts",
    summary="Public List Posts",
    description="List published bulletin board posts with pagination. Returns title, slug, excerpt, author, category, publish date, and metadata. Filters by category and search query.",
)
async def public_list_posts(
    request: Request,
    category: str = "",
    search: str = "",
    tag: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict:
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


@router.get("/posts/{slug}")
async def public_get_post(request: Request, slug: str) -> dict:
    """Get a single published post by slug (public)."""
    post = await BulletinBoardManager.get_post_by_slug(slug)
    if not post or post.status != PostStatus.PUBLISHED.value:
        raise NotFoundError(message="Post not found")

    # Track view
    await BulletinBoardManager.track_engagement(post.post_id, "view")

    return {"post": post.to_public_dict()}


@router.get(
    "/categories",
    summary="Public Categories",
    description="List all post categories with the count of published posts in each. Useful for navigation and filtering.",
)
async def public_categories() -> dict:
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


@router.get(
    "/pinned",
    summary="Public Pinned Posts",
    description="Get pinned posts that are featured at the top of the bulletin board. Pinned posts are manually curated by admins.",
)
async def public_pinned_posts(limit: int = 5) -> dict:
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
