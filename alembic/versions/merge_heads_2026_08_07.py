"""merge migration heads — resolves 2-branch fork into single head

Revision ID: merge_heads_2026_08_07
Revises: a3b4c5d6e7f8, merge_heads_2026_07_27
Create Date: 2026-08-07 15:20:00.000000

This migration merges two parallel branches into a single Alembic
head, enabling ``alembic upgrade head`` (and CI checks) to resolve
deterministically.

Branches merged:
  - a3b4c5d6e7f8 (scraped intel)
  - merge_heads_2026_07_27 (previous 3-branch merge)

Upgrade/downgrade are NOOP — all schema changes live in the
individual branch migrations above.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "merge_heads_2026_08_07"
down_revision: str | Sequence[str] | None = (
    "a3b4c5d6e7f8",
    "merge_heads_2026_07_27",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge two parallel branches — no schema changes."""


def downgrade() -> None:
    """Reverse merge — no schema changes."""
