"""Add body-quality columns to news_items (separate from ratings verdicts).

Revision ID: e4f5a6b7c8d9
Revises: d9e0f1a2b3c4
Create Date: 2026-10-23 00:00:00.000000

``news_items.quality_verdict`` already carries the ratings pipeline's
``ACCEPT | REJECT | NEEDS_SOURCE`` decision, and ``quality_score`` its 0-100
score. Body quality is a DIFFERENT fact (is this body real article text, an
RSS fragment, or a Google News shell?) and therefore gets its own columns:

* ``body_quality_verdict``  ok | thin | stub
* ``body_quality_score``    0-100, from ``app.domains.news.quality.score_body``
* ``body_source``           publisher | truncated | google_shell
* ``truncated``             body is not the full article
* ``canonical_url``         publisher URL after Google-shell resolution
* ``resolved_at``           when resolution was attempted (NULL = pending)

Every statement is idempotent: the live DB already carries some of these
columns from the backfill's bootstrap DDL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "ADD COLUMN IF NOT EXISTS body_quality_verdict TEXT",
    "ADD COLUMN IF NOT EXISTS body_quality_score SMALLINT",
    "ADD COLUMN IF NOT EXISTS body_source TEXT",
    "ADD COLUMN IF NOT EXISTS truncated BOOLEAN NOT NULL DEFAULT false",
    "ADD COLUMN IF NOT EXISTS canonical_url TEXT",
    "ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
)


def upgrade() -> None:
    """Add the body-quality columns, their indexes and the verdict CHECK."""
    for clause in _COLUMNS:
        op.execute(f"ALTER TABLE news_items {clause}")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_body_source ON news_items(body_source)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_news_body_quality_verdict "
        "ON news_items(body_quality_verdict) WHERE body_quality_verdict IS NOT NULL"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE news_items ADD CONSTRAINT ck_news_body_quality_verdict "
        "CHECK (body_quality_verdict IN ('ok','thin','stub')); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )


def downgrade() -> None:
    """Drop only what this migration introduced (ratings columns stay)."""
    op.execute("ALTER TABLE news_items DROP CONSTRAINT IF EXISTS ck_news_body_quality_verdict")
    op.execute("DROP INDEX IF EXISTS idx_news_body_quality_verdict")
    for column in (
        "body_quality_verdict",
        "body_quality_score",
        "canonical_url",
        "resolved_at",
    ):
        op.execute(f"ALTER TABLE news_items DROP COLUMN IF EXISTS {column}")
