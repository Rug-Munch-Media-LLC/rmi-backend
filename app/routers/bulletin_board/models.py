"""Bulletin board request models (shared across public/admin/social routes)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PublicPostRequest(BaseModel):
    """Lightweight model for public discussion posts (no auth required)."""

    title: str = Field("", max_length=200)
    content: str = Field(..., min_length=1, max_length=5000)
    author_name: str = Field("Community Member", max_length=100)
    tags: list[str] = Field(default_factory=list)


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
