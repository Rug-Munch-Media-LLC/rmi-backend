"""
Ghost CMS admin endpoints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from . import verify_admin

logger = logging.getLogger(__name__)

ghost_router = APIRouter()


class DraftCreate(BaseModel):
    """Body for manual draft creation."""

    title: str
    content: str
    post_type: str = "micro"
    tags: list[str] = []


async def _admin_request(
    method: str, path: str, json_body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any] | str]:
    """Authenticated Ghost Admin API request. Returns (status, parsed)."""
    import httpx

    from app.content_platform import GHOST_API, _admin_headers, _get_admin_auth

    auth = _get_admin_auth()
    if not auth:
        return 401, "No Ghost admin credentials"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            method,
            f"{GHOST_API}/ghost/api/admin/{path}",
            headers=_admin_headers(auth),
            json=json_body,
        )
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, resp.text[:300]


@ghost_router.get("/content/drafts")
async def ghost_drafts(
    request: Request, _=Depends(verify_admin), limit: int = Query(20, le=100)
) -> dict[str, list[Any]] | dict[str, Any]:
    """Get Ghost drafts."""
    try:
        import httpx

        from app.content_platform import GHOST_API, GHOST_CONTENT_KEY

        if not GHOST_CONTENT_KEY:
            return {"posts": [], "error": "Ghost content key not configured"}
        params: dict[str, Any] = {
            "key": GHOST_CONTENT_KEY,
            "limit": limit,
            "filter": "status:draft",
            "include": "tags,authors",
            "order": "updated_at DESC",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GHOST_API}/ghost/api/content/posts/",
                params=params,
                headers={"Accept-Version": "v5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                posts = [
                    {
                        "id": p["id"],
                        "title": p["title"],
                        "excerpt": p.get("excerpt", "")[:200],
                        "status": "draft",
                        "updated_at": p.get("updated_at", ""),
                        "tags": [t["name"] for t in p.get("tags", [])],
                    }
                    for p in data.get("posts", [])
                ]
                return {"posts": posts, "total": len(posts)}
            return {"posts": [], "error": f"Ghost HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"Ghost drafts error: {e}")
        return {"posts": [], "error": str(e)}


@ghost_router.get("/content/tags")
async def ghost_tags(request: Request, _=Depends(verify_admin)) -> dict[str, list[Any]] | dict[str, Any]:
    """Get Ghost tags."""
    try:
        import httpx

        from app.content_platform import GHOST_API, GHOST_CONTENT_KEY

        if not GHOST_CONTENT_KEY:
            return {"tags": [], "error": "Ghost content key not configured"}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GHOST_API}/ghost/api/content/tags/",
                params={"key": GHOST_CONTENT_KEY, "limit": 100},
                headers={"Accept-Version": "v5.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tags = [
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "slug": t.get("slug", ""),
                        "post_count": t.get("count", {}).get("posts", 0) if isinstance(t.get("count"), dict) else 0,
                    }
                    for t in data.get("tags", [])
                ]
                return {"tags": tags, "total": len(tags)}
            return {"tags": [], "error": f"Ghost HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"Ghost tags error: {e}")
        return {"tags": [], "error": str(e)}


@ghost_router.get("/content/analytics")
async def ghost_analytics(request: Request, _=Depends(verify_admin)) -> dict[str, Any] | dict[str, str]:
    """Get Ghost analytics computed from local data."""
    try:
        import httpx

        from app.content_platform import GHOST_API, GHOST_CONTENT_KEY

        total_posts = 0
        posts_this_month = 0
        if GHOST_CONTENT_KEY:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{GHOST_API}/ghost/api/content/posts/",
                    params={"key": GHOST_CONTENT_KEY, "limit": 100, "include": "tags", "order": "published_at DESC"},
                    headers={"Accept-Version": "v5.0"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    posts = data.get("posts", [])
                    total_posts = len(posts)
                    now = datetime.now(UTC)
                    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    for p in posts:
                        pub_at = p.get("published_at") or p.get("created_at", "")
                        if pub_at:
                            try:
                                pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                                if pub_dt >= month_start:
                                    posts_this_month += 1
                            except Exception:  # noqa: S110 — intentional best-effort
                                pass
        return {
            "total_posts": total_posts,
            "posts_this_month": posts_this_month,
            "member_count": 0,
            "avg_read_time": 0,
            "views_today": 0,
            "views_this_month": 0,
            "source": "ghost_content_api",
            "note": "Analytics computed from Ghost Content API",
        }
    except Exception as e:
        return {"error": str(e)}


@ghost_router.post("/content/post/{post_id}/publish")
async def ghost_publish(request: Request, post_id: str, _=Depends(verify_admin)) -> dict[str, str] | dict[str, bool]:
    """Publish a Ghost draft post."""
    try:
        import httpx

        from app.content_platform import GHOST_API, _get_session_cookie

        session = _get_session_cookie()
        if not session:
            return {"error": "No Ghost session cookie - login at /ghost first"}
        cookie_header = session.split("\t")
        cookie = f"{cookie_header[5]}={cookie_header[6]}" if len(cookie_header) > 6 else session

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{GHOST_API}/ghost/api/admin/posts/{post_id}/",
                headers={"Content-Type": "application/json", "Origin": GHOST_API, "Cookie": cookie},
                json={"posts": [{"status": "published"}]},
            )
            if resp.status_code in (200, 201):
                p = resp.json()["posts"][0]
                return {"success": True, "id": p["id"], "status": p["status"], "url": p.get("url", "")}
            return {"error": f"Ghost HTTP {resp.status_code}", "detail": resp.text[:300]}
    except Exception as e:
        return {"error": str(e)}


@ghost_router.get("/content/pending")
async def content_pending(request: Request, _=Depends(verify_admin), limit: int = Query(50, le=100)) -> dict[str, Any]:
    """List all pending content: Ghost drafts awaiting approval.

    Bot-side approval requests live in the bot process's in-memory approval
    gate (rugmunchbot scheduler _posts_rag/_approval.py) and are surfaced
    there via Telegram callbacks; they are not visible to the backend.
    """
    try:
        import httpx

        from app.content_platform import GHOST_API, GHOST_CONTENT_KEY

        if not GHOST_CONTENT_KEY:
            return {"pending": [], "error": "Ghost content key not configured"}
        params: dict[str, Any] = {
            "key": GHOST_CONTENT_KEY,
            "limit": limit,
            "filter": "status:draft",
            "include": "tags,authors",
            "order": "updated_at DESC",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GHOST_API}/ghost/api/content/posts/",
                params=params,
                headers={"Accept-Version": "v5.0"},
            )
        if resp.status_code != 200:
            return {"pending": [], "error": f"Ghost HTTP {resp.status_code}"}
        data = resp.json()
        pending = [
            {
                "id": p["id"],
                "title": p["title"],
                "excerpt": p.get("excerpt", "")[:200],
                "status": "draft",
                "updated_at": p.get("updated_at", ""),
                "tags": [t["name"] for t in p.get("tags", [])],
                "source": "ghost",
            }
            for p in data.get("posts", [])
        ]
        return {
            "pending": pending,
            "total": len(pending),
            "note": "Bot approval-gate requests are in-memory in the bot process; approve via Telegram callbacks.",
        }
    except Exception as e:
        logger.warning(f"content pending error: {e}")
        return {"pending": [], "error": str(e)}


@ghost_router.post("/content/{post_id}/approve")
async def content_approve(request: Request, post_id: str, _=Depends(verify_admin)) -> dict[str, Any]:
    """Approve a draft for publication to the main channel (publish it)."""
    try:
        status, data = await _admin_request("PUT", f"posts/{post_id}/", {"posts": [{"status": "published"}]})
        if status in (200, 201) and isinstance(data, dict):
            p = data["posts"][0]
            return {"success": True, "id": p["id"], "status": p["status"], "url": p.get("url", "")}
        if status == 401:
            return {"error": "Ghost admin auth rejected"}
        detail = data if isinstance(data, str) else str(data)[:300]
        return {"error": f"Ghost HTTP {status}", "detail": detail}
    except Exception as e:
        logger.warning(f"content approve error: {e}")
        return {"error": str(e)}


@ghost_router.post("/content/{post_id}/reject")
async def content_reject(
    request: Request, post_id: str, _=Depends(verify_admin), reason: str = Query("")
) -> dict[str, Any]:
    """Reject a draft: delete it from Ghost, logging the reason."""
    try:
        status, data = await _admin_request("DELETE", f"posts/{post_id}/")
        if status in (200, 204):
            logger.info(f"content rejected id={post_id} reason={reason[:200]}")
            return {"success": True, "id": post_id, "rejected": True, "reason": reason}
        if status == 401:
            return {"error": "Ghost admin auth rejected"}
        detail = data if isinstance(data, str) else str(data)[:300]
        return {"error": f"Ghost HTTP {status}", "detail": detail}
    except Exception as e:
        logger.warning(f"content reject error: {e}")
        return {"error": str(e)}


@ghost_router.post("/content/draft")
async def create_draft(request: Request, body: DraftCreate, _=Depends(verify_admin)) -> dict[str, Any]:
    """Create a new Ghost draft post (manual content creation)."""
    from app.content_platform import create_post

    result = await create_post(
        body.content,
        post_type=body.post_type,
        title=body.title,
        tags=body.tags,
        status="draft",
    )
    if "error" in result:
        return result
    return {"success": True, **result}


@ghost_router.get("/content/feed-posts")
async def content_feed_posts(
    request: Request, _=Depends(verify_admin), page: int = Query(1, ge=1), limit: int = Query(20, le=50)
) -> dict[str, Any]:
    """List posts currently live on feed.rugmunch.io."""
    from app.content_platform import get_feed

    return await get_feed(page=page, limit=limit)


@ghost_router.post("/content/{post_id}/feature")
async def content_feature(request: Request, post_id: str, _=Depends(verify_admin)) -> dict[str, Any]:
    """Feature a post on the feed (pin to top via Ghost featured flag)."""
    try:
        status, data = await _admin_request("PUT", f"posts/{post_id}/", {"posts": [{"featured": True}]})
        if status in (200, 201) and isinstance(data, dict):
            p = data["posts"][0]
            return {"success": True, "id": p["id"], "featured": p.get("featured", True)}
        if status == 401:
            return {"error": "Ghost admin auth rejected"}
        detail = data if isinstance(data, str) else str(data)[:300]
        return {"error": f"Ghost HTTP {status}", "detail": detail}
    except Exception as e:
        logger.warning(f"content feature error: {e}")
        return {"error": str(e)}
