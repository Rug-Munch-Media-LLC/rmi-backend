"""Extend news_reactions vocabulary: bullish/bearish/important/scam.

Revision ID: d9e0f1a2b3c4
Revises: cab99e895e61
Create Date: 2026-09-05 00:00:00.000000

The news_reactions.reaction CHECK constraint previously allowed only
'like'/'dislike'. The news reaction UI now supports degen sentiment
reactions (bullish / bearish / important / scam) alongside the legacy
pair, so the constraint is widened (never narrowed on downgrade data).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "cab99e895e61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED = "('like', 'dislike', 'bullish', 'bearish', 'important', 'scam')"


def upgrade() -> None:
    """Widen the reaction CHECK constraint to the full vocabulary."""
    op.execute("ALTER TABLE news_reactions DROP CONSTRAINT IF EXISTS ck_news_reactions_reaction")
    op.execute(f"ALTER TABLE news_reactions ADD CONSTRAINT ck_news_reactions_reaction CHECK (reaction IN {_ALLOWED})")


def downgrade() -> None:
    """Restore the like/dislike-only constraint (fails if wider values exist)."""
    op.execute("ALTER TABLE news_reactions DROP CONSTRAINT IF EXISTS ck_news_reactions_reaction")
    op.execute(
        "ALTER TABLE news_reactions ADD CONSTRAINT ck_news_reactions_reaction CHECK (reaction IN ('like', 'dislike'))"
    )
