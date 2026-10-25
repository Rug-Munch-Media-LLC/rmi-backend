"""ORM models for article engagement: reactions, comments, sentiment rollup.

Portable column types only (``String`` / ``JSON`` / ``DateTime`` / ``Integer``)
so the identical metadata compiles on Postgres (production) and on SQLite (unit
tests) — the test double then exercises the real constraints, not a mock.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] resolves at runtime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.engagement.kinds import REACTION_KINDS
from app.models import Base

COMMENT_STATUSES: tuple[str, ...] = ("visible", "hidden", "removed")

_KIND_SQL = ", ".join(f"'{kind}'" for kind in REACTION_KINDS)
_STATUS_SQL = ", ".join(f"'{status}'" for status in COMMENT_STATUSES)


def new_id() -> str:
    """Opaque public id for a comment row."""
    return str(uuid.uuid4())


class ArticleReaction(Base):
    """One user's vote of one kind on one article — unique per (user, kind)."""

    __tablename__ = "article_reaction"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    article_id: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("article_id", "user_id", "kind", name="uq_article_reaction_user_kind"),
        CheckConstraint(f"kind IN ({_KIND_SQL})", name="ck_article_reaction_kind"),
        Index("ix_article_reaction_article_kind", "article_id", "kind"),
        Index("ix_article_reaction_user", "user_id"),
    )


class ArticleComment(Base):
    """Threaded comment. Depth is one: a reply's parent is always a root."""

    __tablename__ = "article_comment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    article_id: Mapped[str] = mapped_column(String(512), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("article_comment.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="visible", server_default="visible")
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reported_by: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_article_comment_status"),
        Index("ix_article_comment_article_created", "article_id", "created_at"),
        Index("ix_article_comment_parent", "parent_id"),
        Index("ix_article_comment_user", "user_id"),
    )


class ArticleSentimentRollup(Base):
    """Denormalised sentiment per article; ``score`` NULL means no signal."""

    __tablename__ = "article_sentiment_rollup"

    article_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    positive: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    neutral: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    negative: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(48), nullable=False, default="no_reactions")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def as_dict(self) -> dict[str, Any]:
        """Row → plain mapping (keeps the router free of ORM attribute access)."""
        return {
            "article_id": self.article_id,
            "positive": self.positive,
            "neutral": self.neutral,
            "negative": self.negative,
            "sample_size": self.sample_size,
            "score": self.score,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }
