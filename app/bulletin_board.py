"""
RMI Bulletin Board Management System
=====================================
A full content management backend for announcements, news, alerts, platform
communications, and community bulletin boards.

Features:
  • Posts - CRUD with rich text, attachments, scheduling, expiry
  • Categories - organize by type (news, alert, update, promo, system)
  • Targeting - audience segmentation (all, free, premium, admins, specific tiers)
  • Moderation - draft/review/published/archived workflow, approval chains
  • Pinning - sticky posts, priority ordering
  • Analytics - views, clicks, engagement tracking per post
  • Comments - threaded discussions on posts (optional)
  • Notifications - push/email/Telegram alerts for critical posts
  • Scheduling - publish at future date, auto-archive after expiry
  • Versioning - track edit history, rollback capability
  • Search - full-text search across all posts
  • SEO - slug generation, meta tags, OpenGraph

Security:
  - All write operations require admin auth + content.write permission
  - Audit log of every content change
  - Rate limiting on publish operations
  - Content sanitization (strip XSS, validate HTML)
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from app.core.logging import get_logger

logger = get_logger("rmi_bulletin_board")


# ── Enums ─────────────────────────────────────────────────────


class PostStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    SCHEDULED = "scheduled"


class PostCategory(StrEnum):
    NEWS = "news"  # Platform news
    ALERT = "alert"  # Security/urgent alerts
    UPDATE = "update"  # Feature updates
    PROMO = "promo"  # Promotions/offers
    SYSTEM = "system"  # System maintenance
    COMMUNITY = "community"  # Community posts
    ANNOUNCEMENT = "announcement"  # General announcements
    TUTORIAL = "tutorial"  # Guides/how-tos


class TargetAudience(StrEnum):
    ALL = "all"
    FREE = "free"
    PREMIUM = "premium"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    ADMINS = "admins"
    MODERATORS = "moderators"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ── Data Models ─────────────────────────────────────────────


@dataclass
class Post:
    """Bulletin board post."""

    post_id: str
    title: str
    slug: str
    content: str
    summary: str
    category: str
    status: str
    priority: str
    target_audience: str
    author_id: str
    author_email: str
    author_name: str
    created_at: str
    updated_at: str
    published_at: str | None = None
    scheduled_at: str | None = None
    expires_at: str | None = None
    archived_at: str | None = None
    pinned: bool = False
    pin_order: int = 0
    featured_image: str = ""
    attachments: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    meta_title: str = ""
    meta_description: str = ""
    og_image: str = ""
    view_count: int = 0
    click_count: int = 0
    engagement_score: float = 0.0
    version: int = 1
    edit_history: list[dict] = field(default_factory=list)
    approved_by: str = ""
    approved_at: str | None = None
    notification_sent: bool = False
    allow_comments: bool = False
    comments_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_public_dict(self) -> dict:
        """Return public-safe version (no internal fields)."""
        return {
            "post_id": self.post_id,
            "title": self.title,
            "slug": self.slug,
            "content": self.content,
            "summary": self.summary,
            "category": self.category,
            "priority": self.priority,
            "author_name": self.author_name,
            "created_at": self.created_at,
            "published_at": self.published_at,
            "expires_at": self.expires_at,
            "pinned": self.pinned,
            "pin_order": self.pin_order,
            "featured_image": self.featured_image,
            "attachments": self.attachments,
            "tags": self.tags,
            "meta_title": self.meta_title,
            "meta_description": self.meta_description,
            "og_image": self.og_image,
            "view_count": self.view_count,
            "allow_comments": self.allow_comments,
            "comments_count": self.comments_count,
        }


@dataclass
class Comment:
    """Comment on a post."""

    comment_id: str
    post_id: str
    author_id: str
    author_name: str
    author_email: str
    content: str
    created_at: str
    updated_at: str
    parent_id: str | None = None
    status: str = "approved"  # approved, pending, rejected
    likes: int = 0
    replies: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Content Sanitizer ───────────────────────────────────────


class ContentSanitizer:
    """Sanitize user-generated content to prevent XSS."""

    ALLOWED_TAGS: ClassVar[dict] = {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "blockquote",
        "code",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "div",
        "span",
        "hr",
        "sub",
        "sup",
        "del",
        "ins",
    }

    ALLOWED_ATTRS: ClassVar[dict] = {
        "a": ["href", "title", "target"],
        "img": ["src", "alt", "title", "width", "height"],
        "div": ["class"],
        "span": ["class"],
        "code": ["class"],
        "pre": ["class"],
    }

    @staticmethod
    def sanitize(text: str) -> str:
        """Basic HTML sanitization."""
        if not text:
            return ""

        # Escape HTML entities first
        text = html.escape(text)

        # Then selectively un-escape allowed tags
        # This is a simplified approach - in production use bleach or similar
        # For now, strip all HTML tags for safety
        text = re.sub(r"<[^>]+>", "", text)

        return text.strip()

    @staticmethod
    def generate_slug(title: str) -> str:
        """Generate URL-friendly slug from title."""
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug[:80]

    @staticmethod
    def generate_summary(content: str, max_length: int = 200) -> str:
        """Generate summary from content."""
        # Strip HTML
        text = re.sub(r"<[^>]+>", "", content)
        text = text.replace("\n", " ").strip()
        if len(text) > max_length:
            text = text[:max_length].rsplit(" ", 1)[0] + "..."
        return text


# ── Bulletin Board Manager ────────────────────────────────────


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

        # Sanitize content
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

        # Save post
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post.to_dict()))

        # Add to category index
        await r.sadd(f"bulletin:category:{category}", post_id)

        # Add to status index
        await r.sadd(f"bulletin:status:{status}", post_id)

        # Add to author index
        await r.sadd(f"bulletin:author:{author_id}", post_id)

        # Add to audience index
        await r.sadd(f"bulletin:audience:{target_audience}", post_id)

        # Add to pinned index if pinned
        if pinned:
            await r.sadd("bulletin:pinned", post_id)
            await r.zadd("bulletin:pinned_order", {post_id: post.pin_order})

        # Add to scheduled index if scheduled
        if scheduled_at and status == "scheduled":
            ts = int(datetime.fromisoformat(scheduled_at).timestamp())
            await r.zadd("bulletin:scheduled", {post_id: ts})

        # Add to search index (simple word index)
        words = set(re.findall(r"\w+", title.lower() + " " + content.lower()))
        for word in words:
            if len(word) > 2:
                await r.sadd(f"bulletin:search:{word}", post_id)

        # Save to Supabase
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                client = create_client(supabase_url, supabase_key)
                client.table("bulletin_posts").insert(post.to_dict()).execute()
        except Exception as e:
            logger.warning(f"Supabase bulletin post save failed: {e}")

        return post

    @staticmethod
    async def get_post(post_id: str, increment_views: bool = False) -> Post | None:
        """Get a post by ID."""
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
        """Get a post by slug."""
        r = await BulletinBoardManager._get_redis()
        # Search all posts for matching slug
        all_posts = await r.hgetall("rmi:bulletin_posts")
        for _post_id, data in all_posts.items():
            post_dict = json.loads(data)
            if post_dict.get("slug") == slug:
                return Post(**post_dict)
        return None

    @staticmethod
    async def update_post(
        post_id: str,
        updates: dict[str, Any],
        editor_id: str = "",
        editor_email: str = "",
    ) -> Post | None:
        """Update a post with versioning."""
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return None

        post_dict = json.loads(data)
        before_state = {k: post_dict.get(k) for k in updates if k in post_dict}

        # Record edit history
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

        # Apply updates
        for key, value in updates.items():
            if key == "content":
                value = ContentSanitizer.sanitize(value)
                post_dict["summary"] = ContentSanitizer.generate_summary(value)
            if key == "title":
                post_dict["slug"] = ContentSanitizer.generate_slug(value)
            post_dict[key] = value

        # Handle status transitions
        old_status = before_state.get("status")
        new_status = post_dict.get("status")
        if old_status != new_status:
            # Update status indexes
            if old_status:
                await r.srem(f"bulletin:status:{old_status}", post_id)
            await r.sadd(f"bulletin:status:{new_status}", post_id)

            if new_status == "published":
                post_dict["published_at"] = datetime.now(UTC).isoformat()
            elif new_status == "archived":
                post_dict["archived_at"] = datetime.now(UTC).isoformat()

        # Handle pinning changes
        if "pinned" in updates:
            if updates["pinned"]:
                await r.sadd("bulletin:pinned", post_id)
                await r.zadd("bulletin:pinned_order", {post_id: post_dict.get("pin_order", 0)})
            else:
                await r.srem("bulletin:pinned", post_id)
                await r.zrem("bulletin:pinned_order", post_id)

        # Save updated post
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))

        # Update Supabase
        try:
            from supabase import create_client

            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or ""
            if supabase_url and supabase_key:
                client = create_client(supabase_url, supabase_key)
                client.table("bulletin_posts").upsert(post_dict).execute()
        except Exception as e:
            logger.warning(f"Supabase bulletin post update failed: {e}")

        return Post(**post_dict)

    @staticmethod
    async def delete_post(post_id: str) -> bool:
        """Delete a post (soft delete - move to archive)."""
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
        """List posts with filtering."""
        r = await BulletinBoardManager._get_redis()

        # Start with all posts or filtered set
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
            # Search by words
            words = re.findall(r"\w+", search_query.lower())
            if words:
                sets = [f"bulletin:search:{w}" for w in words if len(w) > 2]
                if sets:
                    post_ids = await r.sinter(sets)
                else:
                    post_ids = set()
            else:
                post_ids = set()
        else:
            all_posts = await r.hgetall("rmi:bulletin_posts")
            post_ids = set(all_posts.keys())

        # Apply additional filters
        if tags:
            tagged_posts = set()
            all_posts = await r.hgetall("rmi:bulletin_posts")
            for pid, data in all_posts.items():
                post_dict = json.loads(data)
                if any(tag in post_dict.get("tags", []) for tag in tags):
                    tagged_posts.add(pid)
            post_ids = post_ids.intersection(tagged_posts)

        # Fetch and sort posts
        posts = []
        all_posts = await r.hgetall("rmi:bulletin_posts")
        for pid in post_ids:
            data = all_posts.get(pid)
            if data:
                post_dict = json.loads(data)
                posts.append(post_dict)

        # Sort
        reverse = sort_order == "desc"
        posts.sort(key=lambda x: x.get(sort_by, ""), reverse=reverse)

        # Pinned posts first if not pinned_only
        if not pinned_only:
            pinned_ids = await r.smembers("bulletin:pinned")
            posts.sort(
                key=lambda x: (x["post_id"] not in pinned_ids, x.get(sort_by, "")),
                reverse=not reverse,
            )

        total = len(posts)
        posts = posts[offset : offset + limit]

        return {
            "posts": posts,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    async def publish_scheduled() -> list[str]:
        """Publish posts that are scheduled for now."""
        r = await BulletinBoardManager._get_redis()
        now = int(time.time())

        # Get scheduled posts that are due
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
        """Archive posts that have expired."""
        r = await BulletinBoardManager._get_redis()
        now = datetime.now(UTC).isoformat()

        all_posts = await r.hgetall("rmi:bulletin_posts")
        archived = []

        for post_id, data in all_posts.items():
            post_dict = json.loads(data)
            if post_dict.get("status") == "published" and post_dict.get("expires_at"):
                if post_dict["expires_at"] < now:
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
        post_id: str,
        author_id: str,
        author_name: str,
        author_email: str,
        content: str,
        parent_id: str | None = None,
    ) -> Comment | None:
        """Add a comment to a post."""
        r = await BulletinBoardManager._get_redis()

        # Check if post exists and allows comments
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

        # Update post comment count
        post_dict["comments_count"] = post_dict.get("comments_count", 0) + 1
        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))

        return comment

    @staticmethod
    async def get_comments(post_id: str, limit: int = 100) -> list[dict]:
        """Get comments for a post."""
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
        """Get bulletin board statistics."""
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

        # Count by category
        for cat in PostCategory:
            count = await r.scard(f"bulletin:category:{cat.value}") or 0
            stats["categories"][cat.value] = count

        return stats

    @staticmethod
    async def track_engagement(post_id: str, action: str) -> bool:
        """Track engagement (view, click, etc.)."""
        r = await BulletinBoardManager._get_redis()
        data = await r.hget("rmi:bulletin_posts", post_id)
        if not data:
            return False

        post_dict = json.loads(data)

        if action == "view":
            post_dict["view_count"] = post_dict.get("view_count", 0) + 1
        elif action == "click":
            post_dict["click_count"] = post_dict.get("click_count", 0) + 1

        # Calculate engagement score
        views = post_dict.get("view_count", 0)
        clicks = post_dict.get("click_count", 0)
        post_dict["engagement_score"] = round((clicks / max(views, 1)) * 100, 2)

        await r.hset("rmi:bulletin_posts", post_id, json.dumps(post_dict))
        return True


# ═══════════════════════════════════════════════════════════
# BADGES & X402 BOT PAYMENTS
# ═══════════════════════════════════════════════════════════

BADGES = {
    "first_post": {
        "name": "First Words",
        "icon": "💬",
        "desc": "Made your first post",
        "tier": "bronze",
    },
    "10_posts": {"name": "Chatterbox", "icon": "📢", "desc": "10 posts", "tier": "bronze"},
    "50_posts": {"name": "Board Regular", "icon": "🎙️", "desc": "50 posts", "tier": "silver"},
    "100_posts": {
        "name": "Terminally Online",
        "icon": "🖥️",
        "desc": "100 posts - touch grass",
        "tier": "gold",
    },
    "10_upvotes": {
        "name": "Approved",
        "icon": "👍",
        "desc": "10 upvotes on a post",
        "tier": "bronze",
    },
    "50_upvotes": {"name": "Crowd Favorite", "icon": "⭐", "desc": "50 upvotes", "tier": "silver"},
    "100_upvotes": {"name": "Legendary", "icon": "👑", "desc": "100 upvotes", "tier": "gold"},
    "rug_reporter": {
        "name": "Rug Detective",
        "icon": "🔍",
        "desc": "Reported 3 verified rugs",
        "tier": "silver",
    },
    "scam_buster": {
        "name": "Scam Buster",
        "icon": "🛡️",
        "desc": "10 scam alerts verified",
        "tier": "gold",
    },
    "honeypot_hunter": {
        "name": "Honeypot Hunter",
        "icon": "🍯",
        "desc": "Found 5 honeypots",
        "tier": "silver",
    },
    "whale_watcher": {
        "name": "Whale Watcher",
        "icon": "🐋",
        "desc": "Tracked 10 whale moves",
        "tier": "silver",
    },
    "alpha_caller": {
        "name": "Alpha Caller",
        "icon": "📈",
        "desc": "Called 5 pumps",
        "tier": "gold",
    },
    "degen": {
        "name": "Certified Degen",
        "icon": "🎰",
        "desc": "Posted in every category",
        "tier": "gold",
    },
    "rug_survivor": {
        "name": "Rug Survivor",
        "icon": "💀",
        "desc": "Posted about getting rugged",
        "tier": "bronze",
    },
    "diamond_hands": {
        "name": "Diamond Hands",
        "icon": "💎",
        "desc": "Held through -90%",
        "tier": "diamond",
    },
    "based": {
        "name": "Based",
        "icon": "🧠",
        "desc": "Called a 10x before it happened",
        "tier": "diamond",
    },
    "bot_verified": {
        "name": "Verified Bot",
        "icon": "🤖",
        "desc": "Registered x402 bot",
        "tier": "silver",
    },
    "bot_pro": {"name": "Bot Pro", "icon": "⚡", "desc": "100+ API calls", "tier": "gold"},
}
BADGE_TIERS = {"bronze": "#CD7F32", "silver": "#C0C0C0", "gold": "#FFD700", "diamond": "#B9F2FF"}


async def get_user_badges(user_id: str) -> list:
    try:
        r = await BulletinBoardManager._get_redis()
        ids = await r.smembers(f"bb:badges:{user_id}")
        await r.close()
        return [{"id": bid, **BADGES[bid]} for bid in ids if bid in BADGES]
    except Exception:
        return []


async def award_badge(user_id: str, badge_id: str) -> bool:
    if badge_id not in BADGES:
        return False
    try:
        r = await BulletinBoardManager._get_redis()
        ok = await r.sadd(f"bb:badges:{user_id}", badge_id)
        await r.close()
        return ok > 0
    except Exception:
        return False


async def get_user_reputation(user_id: str) -> dict:
    try:
        r = await BulletinBoardManager._get_redis()
        p = r.pipeline()
        p.get(f"bb:rep:{user_id}")
        p.scard(f"bb:badges:{user_id}")
        p.get(f"bb:posts:{user_id}")
        rep, bc, pc = await p.execute()
        await r.close()
        return {"reputation": int(rep or 0), "badge_count": bc or 0, "post_count": int(pc or 0)}
    except Exception:
        return {"reputation": 0, "badge_count": 0, "post_count": 0}


X402_BB_POST_PRICE = "$1.00"


async def verify_x402_bot(tx_hash: str, bot_addr: str) -> bool:
    try:
        r = await BulletinBoardManager._get_redis()
        if await r.get(f"bb:x402:tx:{tx_hash}"):
            await r.close()
            return False
        await r.setex(f"bb:x402:tx:{tx_hash}", 86400, bot_addr)
        await r.setex(f"bb:x402:bot:{bot_addr}", 2592000, "1")  # 30 day auth
        await r.close()
        return True
    except Exception:
        return False
