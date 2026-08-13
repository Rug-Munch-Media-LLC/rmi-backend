"""Add news comments + reactions tables.

Revision ID: c1d2e3f4a5b6
Revises: d4e5f6a7b8c9
Create Date: 2026-08-13 00:00:00.000000

Tables (NEWS-PAGE-DESIGN.md §3):
  news_reactions          - like/dislike votes on articles (PK news_id+user_id)
  news_comments           - threaded article comments (soft delete via deleted_at)
  news_comment_reactions  - like/dislike votes on comments (PK comment_id+user_id)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create news_reactions, news_comments, news_comment_reactions."""
    # ── news_reactions ────────────────────────────────────────────────
    op.create_table(
        "news_reactions",
        sa.Column("news_id", sa.String(512), nullable=False),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column(
            "reaction",
            sa.String(16),
            nullable=False,
            server_default="like",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reaction IN ('like', 'dislike')", name="ck_news_reactions_reaction"),
        sa.PrimaryKeyConstraint("news_id", "user_id", name="pk_news_reactions"),
    )
    op.create_index("idx_news_reactions_news", "news_reactions", ["news_id"])
    op.create_index("idx_news_reactions_user", "news_reactions", ["user_id"])

    # ── news_comments ─────────────────────────────────────────────────
    op.create_table(
        "news_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("news_id", sa.String(512), nullable=False),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["news_comments.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_news_comments_news", "news_comments", ["news_id"])
    op.create_index("idx_news_comments_parent", "news_comments", ["parent_id"])
    op.create_index("idx_news_comments_user", "news_comments", ["user_id"])
    op.create_index("idx_news_comments_created", "news_comments", ["created_at"])

    # ── news_comment_reactions ────────────────────────────────────────
    op.create_table(
        "news_comment_reactions",
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column(
            "reaction",
            sa.String(16),
            nullable=False,
            server_default="like",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reaction IN ('like', 'dislike')", name="ck_news_comment_reactions_reaction"),
        sa.ForeignKeyConstraint(["comment_id"], ["news_comments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("comment_id", "user_id", name="pk_news_comment_reactions"),
    )
    op.create_index("idx_news_comment_reactions_comment", "news_comment_reactions", ["comment_id"])


def downgrade() -> None:
    """Drop news comments + reactions tables."""
    op.drop_table("news_comment_reactions")
    op.drop_table("news_comments")
    op.drop_table("news_reactions")
