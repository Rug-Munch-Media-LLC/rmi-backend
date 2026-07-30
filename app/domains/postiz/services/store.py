from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class PostizStore:
    """In-memory store for Postiz operations.

    TODO: Replace with Postgres + SQLAlchemy models + Alembic migrations.
    See app/domains/analytics/ for an example of the full DB-backed pattern.
    """

    def __init__(self):
        self._connections: dict[str, dict[str, Any]] = {}  # id → connection
        self._scheduled_posts: dict[str, dict[str, Any]] = {}  # id → post
        self._recurring_posts: dict[str, dict[str, Any]] = {}  # id → recurring
        self._media_items: dict[str, dict[str, Any]] = {}  # id → media
        self._templates: dict[str, dict[str, Any]] = {}  # id → template
        self._analytics_cache: dict[str, dict[str, Any]] = {}
        self._post_counter = 0

    # ── Connections ──────────────────────────────────────────────

    def add_connection(self, connection: dict[str, Any]) -> dict[str, Any]:
        cid = str(uuid.uuid4())
        connection["id"] = cid
        connection["connected_at"] = datetime.now(UTC).isoformat()
        self._connections[cid] = connection
        return connection

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self._connections.get(connection_id)

    def get_connections_by_platform(self, platform_id: str) -> list[dict[str, Any]]:
        return [c for c in self._connections.values() if c.get("platform_id") == platform_id]

    def get_all_connections(self) -> list[dict[str, Any]]:
        return list(self._connections.values())

    def remove_connection(self, connection_id: str) -> bool:
        return self._connections.pop(connection_id, None) is not None

    def get_connected_platform_ids(self) -> set[str]:
        return {c["platform_id"] for c in self._connections.values() if c.get("status") == "active"}

    # ── Scheduled Posts ──────────────────────────────────────────

    def add_post(self, post: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        post["id"] = pid
        now = datetime.now(UTC).isoformat()
        post["created_at"] = now
        post["updated_at"] = now
        post.setdefault("status", "scheduled" if post.get("scheduled_at") else "draft")
        post.setdefault("results", {})
        post.setdefault("failures", {})
        self._scheduled_posts[pid] = post
        self._post_counter += 1
        return post

    def update_post(self, post_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        post = self._scheduled_posts.get(post_id)
        if post is None:
            return None
        updates["updated_at"] = datetime.now(UTC).isoformat()
        post.update(updates)
        return post

    def get_post(self, post_id: str) -> dict[str, Any] | None:
        return self._scheduled_posts.get(post_id)

    def get_posts_by_platform(self, platform_id: str) -> list[dict[str, Any]]:
        return [p for p in self._scheduled_posts.values() if platform_id in p.get("platforms", [])]

    def get_all_posts(
        self,
        status: str | None = None,
        platform: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        posts = list(self._scheduled_posts.values())
        if status:
            posts = [p for p in posts if p.get("status") == status]
        if platform:
            posts = [p for p in posts if platform in p.get("platforms", [])]
        posts.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return posts[offset : offset + limit]

    def get_posts_in_range(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Get posts scheduled in a date range."""
        result = []
        for p in self._scheduled_posts.values():
            scheduled = p.get("scheduled_at", p.get("created_at", ""))
            if scheduled and start_date <= scheduled[:10] <= end_date:
                result.append(p)
        return result

    def get_queue(self) -> list[dict[str, Any]]:
        """Get queued posts sorted by queue position."""
        queue = [p for p in self._scheduled_posts.values() if p.get("status") == "queued"]
        queue.sort(key=lambda p: (p.get("queue_position", 999), p.get("created_at", "")))
        return queue

    def get_last_post_date(self, platform_id: str) -> str | None:
        """Get the date of the last published post on a platform."""
        posts = self.get_posts_by_platform(platform_id)
        published = [p for p in posts if p.get("status") == "published" and p.get("published_at")]
        if not published:
            return None
        published.sort(key=lambda p: p["published_at"], reverse=True)
        return published[0]["published_at"]

    def get_posts_this_period(self, platform_id: str, days: int) -> int:
        """Count posts on a platform in the last N days."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        return sum(
            1
            for p in self._scheduled_posts.values()
            if platform_id in p.get("platforms", []) and (p.get("published_at") or p.get("created_at", "")) >= cutoff
        )

    # ── Recurring Posts ──────────────────────────────────────────

    def add_recurring(self, recurring: dict[str, Any]) -> dict[str, Any]:
        rid = str(uuid.uuid4())
        recurring["id"] = rid
        recurring["created_at"] = datetime.now(UTC).isoformat()
        recurring.setdefault("times_posted", 0)
        recurring.setdefault("status", "active")
        self._recurring_posts[rid] = recurring
        return recurring

    def get_recurring(self, recurring_id: str) -> dict[str, Any] | None:
        return self._recurring_posts.get(recurring_id)

    def get_all_recurring(self) -> list[dict[str, Any]]:
        return list(self._recurring_posts.values())

    def update_recurring(self, recurring_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        rec = self._recurring_posts.get(recurring_id)
        if rec is None:
            return None
        rec.update(updates)
        return rec

    # ── Media ────────────────────────────────────────────────────

    def add_media(self, media: dict[str, Any]) -> dict[str, Any]:
        mid = str(uuid.uuid4())
        media["id"] = mid
        media["created_at"] = datetime.now(UTC).isoformat()
        self._media_items[mid] = media
        return media

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        return self._media_items.get(media_id)

    def get_all_media(
        self,
        media_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        items = list(self._media_items.values())
        if media_type:
            items = [m for m in items if m.get("media_type") == media_type]
        items.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return items[offset : offset + limit]

    def increment_media_usage(self, media_id: str) -> None:
        media = self._media_items.get(media_id)
        if media:
            media["used_in_posts"] = media.get("used_in_posts", 0) + 1

    # ── Templates ────────────────────────────────────────────────

    def add_template(self, template: dict[str, Any]) -> dict[str, Any]:
        tid = str(uuid.uuid4())
        template["id"] = tid
        now = datetime.now(UTC).isoformat()
        template["created_at"] = now
        template["updated_at"] = now
        template.setdefault("usage_count", 0)
        self._templates[tid] = template
        return template

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        return self._templates.get(template_id)

    def get_all_templates(self) -> list[dict[str, Any]]:
        return list(self._templates.values())

    def increment_template_usage(self, template_id: str) -> None:
        tmpl = self._templates.get(template_id)
        if tmpl:
            tmpl["usage_count"] = tmpl.get("usage_count", 0) + 1

    # ── Analytics Cache ──────────────────────────────────────────

    def cache_analytics(self, key: str, data: dict[str, Any]) -> None:
        self._analytics_cache[key] = data

    def get_cached_analytics(self, key: str) -> dict[str, Any] | None:
        return self._analytics_cache.get(key)


# ── Singleton ────────────────────────────────────────────────────────

_store: PostizStore | None = None


def get_store() -> PostizStore:
    """Get or create the singleton PostizStore."""
    global _store
    if _store is None:
        _store = PostizStore()
    return _store
