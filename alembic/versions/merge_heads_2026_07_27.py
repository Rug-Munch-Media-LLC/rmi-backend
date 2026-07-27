"""merge migration heads — resolves 3-branch fork into single head

Revision ID: merge_heads_2026_07_27
Revises: b6e2f4a1c9d0, e7f8a9b0c1d2, f7e8d9c0b1a2
Create Date: 2026-07-27 12:00:00.000000

This migration merges three parallel branches into a single Alembic
head, enabling ``alembic upgrade head`` (and CI checks) to resolve
deterministically.

Branches merged:
  - b6e2f4a1c9d0 (degenfeed schema)
  - e7f8a9b0c1d2 (creator analytics)
  - f7e8d9c0b1a2 (newsletter system)

Upgrade/downgrade are NOOP — all schema changes live in the
individual branch migrations above.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "merge_heads_2026_07_27"
down_revision: Union[str, Sequence[str], None] = (
    "b6e2f4a1c9d0",
    "e7f8a9b0c1d2",
    "f7e8d9c0b1a2",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge three parallel branches — no schema changes."""
    pass


def downgrade() -> None:
    """Reverse merge — no schema changes."""
    pass
