"""Add article engagement tables (reactions, comments, sentiment rollup).

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6, b1c2d3e4f5a6
Create Date: 2026-10-25 00:00:00.000000

Merges the two outstanding heads and adds:

  article_reaction         — one vote of one kind per (article, user, kind)
  article_comment          — depth-1 threads, soft delete, moderation status
  article_sentiment_rollup — denormalised buckets; ``score`` NULL = no signal

``article_id`` is ``String(512)`` to match the existing ``news_reactions`` /
``news_comments`` id space, so news items, legacy news and curated posts all
share one engagement contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = ("a1b2c3d4e5f6", "b1c2d3e4f5a6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "('like', 'dislike', 'bullish', 'bearish', 'important', 'scam')"
_STATUSES = "('visible', 'hidden', 'removed')"


def upgrade() -> None:
    """Create article_reaction, article_comment, article_sentiment_rollup."""
    op.create_table(
        "article_reaction",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("article_id", sa.String(512), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"kind IN {_KINDS}", name="ck_article_reaction_kind"),
        sa.UniqueConstraint("article_id", "user_id", "kind", name="uq_article_reaction_user_kind"),
    )
    op.create_index("ix_article_reaction_article_kind", "article_reaction", ["article_id", "kind"])
    op.create_index("ix_article_reaction_user", "article_reaction", ["user_id"])

    op.create_table(
        "article_comment",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("article_id", sa.String(512), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="visible"),
        sa.Column("report_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reported_by", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_article_comment_status"),
        sa.ForeignKeyConstraint(["parent_id"], ["article_comment.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_article_comment_article_created", "article_comment", ["article_id", "created_at"])
    op.create_index("ix_article_comment_parent", "article_comment", ["parent_id"])
    op.create_index("ix_article_comment_user", "article_comment", ["user_id"])

    op.create_table(
        "article_sentiment_rollup",
        sa.Column("article_id", sa.String(512), primary_key=True),
        sa.Column("positive", sa.Integer, nullable=False, server_default="0"),
        sa.Column("neutral", sa.Integer, nullable=False, server_default="0"),
        sa.Column("negative", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("reason", sa.String(48), nullable=False, server_default="no_reactions"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Drop the three engagement tables (indexes go with them)."""
    op.drop_table("article_sentiment_rollup")
    op.drop_table("article_comment")
    op.drop_table("article_reaction")
