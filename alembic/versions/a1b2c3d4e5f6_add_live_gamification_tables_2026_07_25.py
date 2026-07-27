"""add_live_gamification_tables_2026_07_25

Revision ID: a1b2c3d4e5f6
Revises: 75c982fccbb8
Create Date: 2026-07-25 12:00:00.000000

Creates 9 tables for the live gamification system:
  - live_battles               (creator battles)
  - battle_gifts               (battle gift scoring)
  - viewer_xp_transactions     (XP earning events)
  - challenges                 (daily/weekly challenge templates)
  - user_challenges            (per-user challenge progress)
  - achievement_defs           (achievement definitions)
  - user_achievements          (per-user achievement progress)
  - creator_streaks            (creator streaming streaks)
  - live_events                (scheduled live events)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "75c982fccbb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = ("b6e2f4a1c9d0",)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # live_battles                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "live_battles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator1_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("creator2_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("timer_duration", sa.Integer, nullable=False, server_default="180"),
        sa.Column("points_1", sa.Integer, nullable=False, server_default="0"),
        sa.Column("points_2", sa.Integer, nullable=False, server_default="0"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("winner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # battle_gifts                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "battle_gifts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "battle_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("live_battles.id"), nullable=False, index=True
        ),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("gift_type", sa.String(64), nullable=False),
        sa.Column("value_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("points", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # viewer_xp_transactions                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "viewer_xp_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_xp_user_created", "viewer_xp_transactions", ["user_id", sa.text("created_at DESC")])

    # ------------------------------------------------------------------ #
    # challenges                                                          #
    # ------------------------------------------------------------------ #
    op.create_table(
        "challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("challenge_type", sa.String(32), nullable=False),
        sa.Column("requirement_value", sa.Integer, nullable=False),
        sa.Column("reward_xp", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reward_badge", sa.String(64), nullable=True),
        sa.Column("reward_gift_discount_pct", sa.Integer, nullable=True),
        sa.Column("reset_frequency", sa.String(16), nullable=False, server_default="daily"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("emoji", sa.String(8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # user_challenges                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "user_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("challenges.id"), nullable=False),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_user_challenge_period",
        "user_challenges",
        ["user_id", "challenge_id", "period_start"],
    )

    # ------------------------------------------------------------------ #
    # achievement_defs                                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "achievement_defs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(64), unique=True, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("target_value", sa.Integer, nullable=False),
        sa.Column("badge_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # user_achievements                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "user_achievements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("achievement_key", sa.String(64), nullable=False),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unlocked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_user_achievement",
        "user_achievements",
        ["user_id", "achievement_key"],
    )

    # ------------------------------------------------------------------ #
    # creator_streaks                                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "creator_streaks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=True, index=True
        ),
        sa.Column("streak_type", sa.String(32), nullable=False, server_default="stream"),
        sa.Column("current_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("longest_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_activity_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ------------------------------------------------------------------ #
    # live_events                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "live_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("hashtag", sa.String(64), nullable=True),
        sa.Column("thumbnail_url", sa.String(512), nullable=True),
        sa.Column("max_viewers", sa.Integer, nullable=True),
        sa.Column("total_viewers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_events_scheduled", "live_events", ["scheduled_at"])
    op.create_index("idx_events_status", "live_events", ["status"])


def downgrade() -> None:
    """Drop all live gamification tables."""
    op.drop_table("live_events")
    op.drop_table("creator_streaks")
    op.drop_table("user_achievements")
    op.drop_table("achievement_defs")
    op.drop_table("user_challenges")
    op.drop_table("challenges")
    op.drop_table("viewer_xp_transactions")
    op.drop_table("battle_gifts")
    op.drop_table("live_battles")
