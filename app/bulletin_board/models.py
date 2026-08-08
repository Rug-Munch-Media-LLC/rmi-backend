"""Bulletin board data models and enums."""

from __future__ import annotations
from typing import Any

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class PostStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    SCHEDULED = "scheduled"


class PostCategory(StrEnum):
    NEWS = "news"
    ALERT = "alert"
    UPDATE = "update"
    PROMO = "promo"
    SYSTEM = "system"
    COMMUNITY = "community"
    ANNOUNCEMENT = "announcement"
    TUTORIAL = "tutorial"


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
    status: str = "approved"
    likes: int = 0
    replies: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
