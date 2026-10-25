"""Request/response schemas for article engagement.

Sanitisation is not re-implemented here: ``sanitize_body`` delegates to the news
domain's single canonical implementation so both surfaces strip HTML identically.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic resolves annotations at runtime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.engagement.kinds import ReactionKind  # noqa: TC001 — runtime annotation
from app.domains.news.routes.schemas import _COMMENT_BODY_MAX, _sanitize_comment_body

CommentStatus = Literal["visible", "hidden", "removed"]

_COMMENT_PAGE_MAX = 100


def sanitize_body(raw: str) -> str:
    """Strip HTML/control chars, collapse whitespace, cap the length."""
    return _sanitize_comment_body(raw)


class ReactionRequest(BaseModel):
    """One reaction kind. Repeating it is an idempotent no-op."""

    model_config = ConfigDict(extra="forbid")

    kind: ReactionKind


class ReactionSummary(BaseModel):
    article_id: str
    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0
    my_reactions: list[str] = Field(default_factory=list)


class ReactorOut(BaseModel):
    user_id: str
    created_at: datetime


class ReactorsResponse(BaseModel):
    kind: str
    items: list[ReactorOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=_COMMENT_BODY_MAX)
    parent_id: str | None = None


class CommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=_COMMENT_BODY_MAX)


class StatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CommentStatus


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class ReportResponse(BaseModel):
    status: str
    report_count: int = 0


class CommentOut(BaseModel):
    """A comment as the API exposes it. ``body`` is None once deleted."""

    id: str
    article_id: str
    parent_id: str | None = None
    user_id: str
    status: str = "visible"
    body: str | None = None
    created_at: datetime
    edited_at: datetime | None = None
    deleted: bool = False
    report_count: int = 0
    is_mine: bool = False
    replies: list[CommentOut] = Field(default_factory=list)


class CommentQuery(BaseModel):
    """Query parameters for one page of comments (a dependency, not five route args)."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(20, ge=1, le=_COMMENT_PAGE_MAX)
    offset: int = Field(0, ge=0)
    sort: Literal["oldest", "newest"] = "oldest"


class CommentListResponse(BaseModel):
    items: list[CommentOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0
    has_more: bool = False


class SentimentResponse(BaseModel):
    """``score`` is null when the evidence is too thin — never a fabricated 0."""

    article_id: str
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    sample_size: int = 0
    score: float | None = None
    reason: str = "no_reactions"
    updated_at: datetime | None = None


CommentOut.model_rebuild()
