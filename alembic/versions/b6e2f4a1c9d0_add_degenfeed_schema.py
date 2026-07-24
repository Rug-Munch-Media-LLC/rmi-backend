"""add_degenfeed_schema — DegenFeed cross-protocol storage tables

Revision ID: b6e2f4a1c9d0
Revises: 75c982fccbb8
Create Date: 2026-07-24 10:00:00.000000

This migration creates the shared storage schema for DegenFeed protocol
operations. Tables are prefixed with ``degenfeed_`` to namespace them
from existing RMI tables.

Tables created:
  - degenfeed_users       — Cross-protocol user identity registry
  - degenfeed_posts       — Cross-protocol post/status data
  - degenfeed_identities  — Protocol-to-protocol identity resolution
  - degenfeed_rate_limits — Per-protocol rate limit tracking

All tables use UUIDv4 primary keys and carry created_at/updated_at
timestamps for replication and CDC compatibility.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6e2f4a1c9d0"
down_revision: str | Sequence[str] | None = "75c982fccbb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the DegenFeed shared storage schema."""

    # ------------------------------------------------------------------ #
    # degenfeed_users — cross-protocol user identity registry             #
    # ------------------------------------------------------------------ #
    # Users who have linked their identities across multiple protocols.
    # Each row represents a single human who may have handles on multiple
    # protocols (linked via the profiles JSON column).
    op.create_table(
        "degenfeed_users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("primary_handle", sa.String(length=128), nullable=False),
        sa.Column("primary_protocol", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column(
            "profiles",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Map of protocol -> profile data for linked identities",
        ),
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("following_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last activity timestamp across any protocol",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_degenfeed_users_primary_handle"),
        "degenfeed_users",
        ["primary_handle"],
        unique=False,
    )
    op.create_index(
        op.f("ix_degenfeed_users_primary_protocol"),
        "degenfeed_users",
        ["primary_protocol"],
        unique=False,
    )
    op.create_index(
        "ix_degenfeed_users_active",
        "degenfeed_users",
        ["is_active", "last_active_at"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # degenfeed_posts — cross-protocol post/status data                   #
    # ------------------------------------------------------------------ #
    # Posts fetched from or published to DegenFeed protocols.
    # The `protocol_data` JSON column stores protocol-specific metadata.
    op.create_table(
        "degenfeed_posts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("author_handle", sa.String(length=128), nullable=False),
        sa.Column("author_protocol", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("media_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("likes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("shares", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("replies", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "protocol_data",
            sa.JSON(),
            nullable=True,
            comment="Protocol-specific metadata (raw response, thread info, etc.)",
        ),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("is_cross_post", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cross_post_source", sa.String(length=32), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id", "protocol", name="uq_degenfeed_post_external"),
    )
    op.create_index(
        op.f("ix_degenfeed_posts_author_id"),
        "degenfeed_posts",
        ["author_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_degenfeed_posts_protocol"),
        "degenfeed_posts",
        ["protocol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_degenfeed_posts_posted_at"),
        "degenfeed_posts",
        ["posted_at"],
        unique=False,
    )
    op.create_index(
        "ix_degenfeed_posts_protocol_posted",
        "degenfeed_posts",
        ["protocol", "posted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_degenfeed_posts_text_hash"),
        "degenfeed_posts",
        ["text_hash"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # degenfeed_identities — protocol-to-protocol identity resolution      #
    # ------------------------------------------------------------------ #
    # Maps a (protocol, handle) pair to a degenfeed_users UUID, enabling
    # cross-protocol identity resolution (e.g., @vitalik on Farcaster =
    # @VitalikButerin on Twitter = vitalik.eth on Lens).
    op.create_table(
        "degenfeed_identities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("handle", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("following_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("user_id", sa.UUID(), nullable=True, comment="FK to degenfeed_users.id"),
        sa.Column(
            "raw_data",
            sa.JSON(),
            nullable=True,
            comment="Raw profile data from the protocol API",
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last time this identity was resolved from the protocol",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("protocol", "handle", name="uq_degenfeed_identity"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["degenfeed_users.id"],
            name="fk_degenfeed_identities_user",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_degenfeed_identities_user_id"),
        "degenfeed_identities",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_degenfeed_identities_protocol_handle",
        "degenfeed_identities",
        ["protocol", "handle"],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # degenfeed_rate_limits — per-protocol rate limit tracking            #
    # ------------------------------------------------------------------ #
    # Tracks rate limit consumption per protocol per user/IP.
    # Used for display purposes and to coordinate cross-protocol throttling.
    op.create_table(
        "degenfeed_rate_limits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("burst_size", sa.Integer(), nullable=False),
        sa.Column("minute_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("day_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("protocol", "client_key", name="uq_degenfeed_rate_limit"),
    )
    op.create_index(
        op.f("ix_degenfeed_rate_limits_protocol"),
        "degenfeed_rate_limits",
        ["protocol"],
        unique=False,
    )


def downgrade() -> None:
    """Drop all DegenFeed tables."""
    op.drop_table("degenfeed_rate_limits")
    op.drop_table("degenfeed_identities")
    op.drop_table("degenfeed_posts")
    op.drop_table("degenfeed_users")
