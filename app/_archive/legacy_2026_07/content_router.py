"""Content API - Ghost-powered unified feed."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1/content", tags=["content"])


@router.post("/post")
async def create_post(request: Request, data: dict):
    from app.content_platform import create_post

    return await create_post(
        content=data.get("content", ""),
        title=data.get("title", ""),
        tags=data.get("tags", []),
        post_type=data.get("post_type", "micro"),
        status=data.get("status", "published"),
    )


@router.get("/feed")
async def feed(request: Request, page: int = 1, limit: int = 20):
    from app.content_platform import get_feed

    return await get_feed(page=page, limit=limit)


@router.get("/posts")
async def posts(request: Request, page: int = 1, limit: int = 20, tag: str = ""):
    from app.content_platform import get_posts

    return await get_posts(page=page, limit=limit, tag=tag)


@router.get("/profile")
async def profile(request: Request):
    from app.content_platform import PROFILE

    return PROFILE


@router.get("/health")
async def content_health(request: Request):
    from app.content_platform import check_health

    return await check_health()
