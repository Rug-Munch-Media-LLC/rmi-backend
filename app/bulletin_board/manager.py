"""Core bulletin board manager — CRUD, search, comments, stats, engagement."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

from .models import Comment, Post, PostCategory
from .sanitizer import ContentSanitizer

logger = get_logger("rmi_bulletin_board")


class BulletinBoardManager:
    """
    Core manager for bulletin board operations.
    Uses Redis as primary store + Supabase backup.
    """

    @staticmethod
    async def _get_redis() -> Any:
        import redis.asyncio as redis_lib

        return redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )

    @staticmethod
    async def create_post(
        title: str,
        content: str,
        category: str,
        author_id: str,
        author_email: str,
        author_name: str = "",
        priority: str = "normal",
        target_audience: str = "all",
        status: str = "draft",
        featured_image: str = "",
        attachments: list[dict] | None = None,
        tags: list[str] | None = None,
        scheduled_at: str | None = None,
        expires_at: str | None = None,
        pinned: bool = False,
        allow_comments: bool = False,
        meta_title: str = "",
        meta_description: str = "",
    ) -> Post:
        """Create a new post."""
        post_id = f"post_{int(time.time())}_{os.urandom(4).hex()}"
        slug = ContentSanitizer.generate_slug(title)
        summary = ContentSanitizer.generate_summary(content)
        now = datetime.now(UTC).isoformat()
        content = ContentSanitizer.sanitize(content)

        post = Post(
            post_id=post_id,
            title=title[:200],
            slug=slug,
            content=content,
            summary=summary,
            category=category,
            status=status,
            priority=priority,
            target_audience=target_audience,
            author_id=author_id,
            author_email=author_email,
            author_name=author_name or author_email.split("@")[0],
            created_at=now,
            updated_at=now,
            scheduled_at=scheduled_at,
            expires_at=expires_at,
            pinned=pinned,
            featured_image=featured_image,
            attachments=attachments or [],
            tags=tags or [],
            meta_title=meta_title or title[:70],
            meta_description=meta_description or summary[:160],
            allow_comments=allow_comments,
        )

        r = await BulletinBoardManager._get_redis()
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post.to_dict()))
        await r.sadd(f"bulletin:category:{category}", post_id)
        await r.sadd(f"bulletin:status:{status}", post_id)
        await r.sadd(f"bulletin:author:{author_id}", post_id)
        await r.sadd(f"bulletin:audience:{target_audience}", post_id)

        if pinned:
            await r.sadd("bulletin:pinned", post_id)
            await r.zadd("bulletin:pinned_order", {post_id: post.pin_order})
        if scheduled_at and status == "scheduled":
            ts = int(datetime.fromisoformat(scheduled_at).timestamp())
            await r.zadd("bulletin:scheduled", {post_id: ts})

        # Search index
        words = set(re.findall(r"\w+", title.lower() + " " + content.lower()))
        for word in words:
            if len(word) > 2:
                await r.sadd(f"bulletin:search:{word}", post_id)

        # Supabase backup
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                create_client(supabase_url, supabase_key).table("bulletin_posts").insert(post.to_dict()).execute()
        except Exception as e:
            logger.warning(f"Supabase bulletin post save failed: {e}")

        return post

    @staticmethod
    async def get_post(post_id: str, increment_views: bool = False) -> Post | None:
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return None
        post_dict = json.loads(data)
        if increment_views and post_dict.get("status") == "published":
            post_dict["view_count"] = post_dict.get("view_count", 0) + 1
            await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
        return Post(**post_dict)

    @staticmethod
    async def get_post_by_slug(slug: str) -> Post | None:
        r = await BulletinBoardManager._get_redis()
        all_posts = await r.hgetall("rmi:bulletin_posts")
        for _post_id, data in all_posts.items():
            post_dict = json.loads(data)
            if post_dict.get("slug") == slug:
                return Post(**post_dict)
        return None

    @staticmethod
    async def update_post(
        post_id: str, updates: dict[str, Any], editor_id: str = "", editor_email: str = ""
    ) -> Post | None:
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return None
        post_dict = json.loads(data)
        before_state = {k: post_dict.get(k) for k in updates if k in post_dict}

        edit_entry = {
            "edited_at": datetime.now(UTC).isoformat(),
            "edited_by": editor_id,
            "editor_email": editor_email,
            "changes": before_state,
            "version": post_dict.get("version", 1),
        }
        history = post_dict.get("edit_history", [])
        history.append(edit_entry)
        post_dict["edit_history"] = history
        post_dict["version"] = post_dict.get("version", 1) + 1
        post_dict["updated_at"] = datetime.now(UTC).isoformat()

        for key, value in updates.items():
            if key == "content":
                value = ContentSanitizer.sanitize(value)
                post_dict["summary"] = ContentSanitizer.generate_summary(value)
            if key == "title":
                post_dict["slug"] = ContentSanitizer.generate_slug(value)
            post_dict[key] = value

        old_status = before_state.get("status")
        new_status = post_dict.get("status")
        if old_status != new_status:
            if old_status:
                await r.srem(f"bulletin:status:{old_status}", post_id)
            await r.sadd(f"bulletin:status:{new_status}", post_id)
            if new_status == "published":
                post_dict["published_at"] = datetime.now(UTC).isoformat()
            elif new_status == "archived":
                post_dict["archived_at"] = datetime.now(UTC).isoformat()

        if "pinned" in updates:
            if updates["pinned"]:
                await r.sadd("bulletin:pinned", post_id)
                await r.zadd("bulletin:pinned_order", {post_id: post_dict.get("pin_order", 0)})
            else:
                await r.srem("bulletin:pinned", post_id)
                await r.zrem("bulletin:pinned_order", post_id)

        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))

        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                create_client(supabase_url, supabase_key).table("bulletin_posts").upsert(post_dict).execute()
        except Exception as e:
            logger.warning(f"Supabase bulletin post update failed: {e}")

        return Post(**post_dict)

    @staticmethod
    async def delete_post(post_id: str) -> bool:
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return False
        post_dict = json.loads(data)
        post_dict["status"] = "archived"
        post_dict["archived_at"] = datetime.now(UTC).isoformat()
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
        await r.srem("bulletin:status:published", post_id)
        await r.srem("bulletin:status:draft", post_id)
        await r.srem("bulletin:status:review", post_id)
        await r.sadd("bulletin:status:archived", post_id)
        await r.srem("bulletin:pinned", post_id)
        return True

    @staticmethod
    async def list_posts(
        category: str | None = None,
        status: str | None = None,
        target_audience: str | None = None,
        author_id: str | None = None,
        pinned_only: bool = False,
        search_query: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        r = await BulletinBoardManager._get_redis()
        if pinned_only:
            post_ids = await r.smembers("bulletin:pinned")
        elif category:
            post_ids = await r.smembers(f"bulletin:category:{category}")
        elif status:
            post_ids = await r.smembers(f"bulletin:status:{status}")
        elif author_id:
            post_ids = await r.smembers(f"bulletin:author:{author_id}")
        elif target_audience:
            post_ids = await r.smembers(f"bulletin:audience:{target_audience}")
        elif search_query:
            words = re.findall(r"\w+", search_query.lower())
            if words:
                sets = [f"bulletin:search:{w}" for w in words if len(w) > 2]
                post_ids = await r.sinter(sets) if sets else set()
            else:
                post_ids = set()
        else:
            all_posts = await r.hgetall("rmi:bulletin_posts")
            post_ids = set(all_posts.keys())

        if tags:
            all_posts = await r.hgetall("rmi:bulletin_posts")
            post_ids = {
                pid
                for pid in post_ids
                if pid in all_posts and any(tag in json.loads(all_posts[pid]).get("tags", []) for tag in tags)
            }

        all_posts = await r.hgetall("rmi:bulletin_posts")
        posts = [json.loads(all_posts[pid]) for pid in post_ids if pid in all_posts]

        reverse = sort_order == "desc"
        posts.sort(key=lambda x: x.get(sort_by, ""), reverse=reverse)
        if not pinned_only:
            pinned_ids = await r.smembers("bulletin:pinned")
            posts.sort(key=lambda x: (x["post_id"] not in pinned_ids, x.get(sort_by, "")), reverse=not reverse)

        total = len(posts)
        posts = posts[offset : offset + limit]
        return {"posts": posts, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    async def publish_scheduled() -> list[str]:
        r = await BulletinBoardManager._get_redis()
        now = int(time.time())
        due = await r.zrangebyscore("bulletin:scheduled", 0, now)
        published = []
        for post_id in due:
            data = await r.hget("rmi:bulletin_posts", post_id)
            if data:
                post_dict = json.loads(data)
                post_dict["status"] = "published"
                post_dict["published_at"] = datetime.now(UTC).isoformat()
                post_dict["scheduled_at"] = None
                await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
                await r.srem("bulletin:status:scheduled", post_id)
                await r.sadd("bulletin:status:published", post_id)
                await r.zrem("bulletin:scheduled", post_id)
                published.append(post_id)
        return published

    @staticmethod
    async def archive_expired() -> list[str]:
        r = await BulletinBoardManager._get_redis()
        now = datetime.now(UTC).isoformat()
        all_posts = await r.hgetall("rmi:bulletin_posts")
        archived = []
        for post_id, data in all_posts.items():
            post_dict = json.loads(data)
            if post_dict.get("status") == "published" and post_dict.get("expires_at") and post_dict["expires_at"] < now:
                post_dict["status"] = "archived"
                post_dict["archived_at"] = now
                await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
                await r.srem("bulletin:status:published", post_id)
                await r.sadd("bulletin:status:archived", post_id)
                await r.srem("bulletin:pinned", post_id)
                archived.append(post_id)
        return archived

    @staticmethod
    async def add_comment(
        post_id: str, author_id: str, author_name: str, author_email: str, content: str, parent_id: str | None = None
    ) -> Comment | None:
        r = await BulletinBoardManager._get_redis()
        post_data = await r.hget("rmi:bulletin_posts", post_id)
        if not post_data:
            return None
        post_dict = json.loads(post_data)
        if not post_dict.get("allow_comments", False):
            return None
        comment_id = f"comment_{int(time.time())}_{os.urandom(4).hex()}"
        now = datetime.now(UTC).isoformat()
        comment = Comment(
            comment_id=comment_id,
            post_id=post_id,
            author_id=author_id,
            author_name=author_name,
            author_email=author_email,
            content=ContentSanitizer.sanitize(content)[:2000],
            created_at=now,
            updated_at=now,
            parent_id=parent_id,
        )
        await r.hset("rmi:bulletin_comments", comment_id, json.dumps(comment.to_dict()))
        await r.sadd(f"bulletin:post_comments:{post_id}", comment_id)
        post_dict["comments_count"] = post_dict.get("comments_count", 0) + 1
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
        return comment

    @staticmethod
    async def get_comments(post_id: str, limit: int = 100) -> list[dict]:
        r = await BulletinBoardManager._get_redis()
        comment_ids = await r.smembers(f"bulletin:post_comments:{post_id}")
        comments = []
        for cid in comment_ids:
            data = await r.hget("rmi:bulletin_comments", cid)
            if data:
                comments.append(json.loads(data))
        comments.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return comments[:limit]

    @staticmethod
    async def get_stats() -> dict[str, Any]:
        r = await BulletinBoardManager._get_redis()
        stats = {
            "total_posts": await r.hlen("rmi:bulletin_posts") or 0,
            "published": await r.scard("bulletin:status:published") or 0,
            "drafts": await r.scard("bulletin:status:draft") or 0,
            "review": await r.scard("bulletin:status:review") or 0,
            "archived": await r.scard("bulletin:status:archived") or 0,
            "scheduled": await r.scard("bulletin:status:scheduled") or 0,
            "pinned": await r.scard("bulletin:pinned") or 0,
            "total_comments": await r.hlen("rmi:bulletin_comments") or 0,
            "categories": {},
        }
        for cat in PostCategory:
            stats["categories"][cat.value] = await r.scard(f"bulletin:category:{cat.value}") or 0
        return stats

    @staticmethod
    async def track_engagement(post_id: str, action: str) -> bool:
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return False
        post_dict = json.loads(data)
        if action == "view":
            post_dict["view_count"] = post_dict.get("view_count", 0) + 1
        elif action == "click":
            post_dict["click_count"] = post_dict.get("click_count", 0) + 1
        views = post_dict.get("view_count", 0)
        clicks = post_dict.get("click_count", 0)
        post_dict["engagement_score"] = round((clicks / max(views, 1)) * 100, 2)
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
        return True
