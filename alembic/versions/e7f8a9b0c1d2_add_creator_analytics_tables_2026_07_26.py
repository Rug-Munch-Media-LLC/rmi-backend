"""add_creator_analytics_tables_2026_07_26

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-07-26 10:00:00.000000

Creates 5 tables for the Creator Analytics system:
  - creator_profiles       (creator identity across protocols)
  - content_posts          (content metadata with cross-protocol IDs)
  - view_events            (true view events with dedup, watch time, drop-off)
  - engagement_events      (likes, comments, reposts, tips, saves, clicks)
  - audience_followers     (follower tracking with churn, demographics, scoring)

Design principles:
  - Every view count is DEDUPLICATED (session_id + viewer_fingerprint + post_id)
  - No "X-style inflation" — every number is a real human
  - Cross-protocol by design (nostr, farcaster, bluesky, mastodon columns)
  - Privacy-first: viewer IP is hashed, not stored raw
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # creator_profiles                                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "creator_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("wallet_address", sa.String(256), nullable=True, index=True),
        sa.Column("display_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("handle", sa.String(128), nullable=False, server_default="", index=True),
        sa.Column("bio", sa.Text, nullable=False, server_default=""),
        sa.Column("avatar_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("email", sa.String(256), nullable=False, server_default=""),
        sa.Column(
            "protocol_handles",
            sa.String(4096),
            nullable=True,
            comment='JSON: {"nostr": "...", "farcaster": "...", "bluesky": "...", "mastodon": "..."}',
        ),
        sa.Column("interests", sa.String(2048), nullable=True, comment="JSON array of creator interests/topics"),
        sa.Column("total_followers", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_posts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_creator_wallet", "creator_profiles", ["wallet_address"])
    op.create_index("ix_creator_handle", "creator_profiles", ["handle"])

    # ------------------------------------------------------------------ #
    # content_posts                                                       #
    # ------------------------------------------------------------------ #
    op.create_table(
        "content_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("creator_profiles.id"), nullable=False),
        sa.Column(
            "external_id", sa.String(256), nullable=False, server_default="", comment="ID on the source platform"
        ),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "content_type",
            sa.String(32),
            nullable=False,
            server_default="micro",
            comment="micro, article, video, image, thread, audio, newsletter",
        ),
        sa.Column(
            "content_length",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Character count or video duration in seconds",
        ),
        sa.Column(
            "content_hash", sa.String(64), nullable=False, server_default="", comment="SHA-256 of content for dedup"
        ),
        sa.Column("tags", sa.String(2048), nullable=False, server_default="", comment="Comma-separated tags"),
        sa.Column(
            "published_protocols",
            sa.String(256),
            nullable=False,
            server_default="",
            comment="Comma-separated: nostr,farcaster,bluesky,mastodon",
        ),
        sa.Column("has_images", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("has_video", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("video_duration_seconds", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_link", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_live", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_paid", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nostr_id", sa.String(256), nullable=False, server_default=""),
        sa.Column("farcaster_hash", sa.String(256), nullable=False, server_default=""),
        sa.Column("bluesky_rkey", sa.String(256), nullable=False, server_default=""),
        sa.Column("mastodon_id", sa.String(256), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_content_creator", "content_posts", ["creator_id"])
    op.create_index("ix_content_type", "content_posts", ["content_type"])
    op.create_index("ix_content_published", "content_posts", ["published_at"])
    op.create_index("ix_content_creator_published", "content_posts", ["creator_id", "published_at"])

    # ------------------------------------------------------------------ #
    # view_events — the foundation of TRUE view counts                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        "view_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_posts.id"), nullable=False),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("creator_profiles.id"), nullable=False),
        sa.Column(
            "viewer_fingerprint",
            sa.String(128),
            nullable=False,
            server_default="",
            index=True,
            comment="Hashed unique viewer identifier (wallet/IP/fingerprint)",
        ),
        sa.Column("viewer_wallet", sa.String(256), nullable=False, server_default=""),
        sa.Column(
            "viewer_ip_hash",
            sa.String(64),
            nullable=False,
            server_default="",
            comment="Hashed IP for unique counting (privacy-safe)",
        ),
        sa.Column(
            "session_id",
            sa.String(128),
            nullable=False,
            server_default="",
            index=True,
            comment="Client-generated session ID for dedup",
        ),
        sa.Column("watch_time_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_content_duration", sa.Float, nullable=False, server_default="0"),
        sa.Column("dropped_at_seconds", sa.Float, nullable=True, comment="Where viewer stopped watching (seconds)"),
        sa.Column(
            "drop_off_point_pct", sa.Float, nullable=True, comment="Percentage through content where viewer dropped"
        ),
        sa.Column("viewed_to_end", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "scrolled_past",
            sa.Boolean,
            nullable=False,
            server_default="false",
            comment="Scrolled past without meaningful watch",
        ),
        sa.Column("scroll_depth_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "protocol",
            sa.String(32),
            nullable=False,
            server_default="nostr",
            index=True,
            comment="nostr, farcaster, bluesky, mastodon, direct",
        ),
        sa.Column("is_live_view", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_replay", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("peak_concurrent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("referrer", sa.String(1024), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_view_post", "view_events", ["post_id"])
    op.create_index("ix_view_creator", "view_events", ["creator_id"])
    op.create_index("ix_view_creator_protocol", "view_events", ["creator_id", "protocol"])
    op.create_index("ix_view_fingerprint_post", "view_events", ["viewer_fingerprint", "post_id"])
    op.create_index("ix_view_created_at", "view_events", ["created_at"])
    op.create_unique_constraint(
        "uq_view_session",
        "view_events",
        ["session_id", "viewer_fingerprint", "post_id"],
    )

    # ------------------------------------------------------------------ #
    # engagement_events                                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "engagement_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_posts.id"), nullable=False),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("creator_profiles.id"), nullable=False),
        sa.Column("viewer_fingerprint", sa.String(128), nullable=False, server_default="", index=True),
        sa.Column("viewer_wallet", sa.String(256), nullable=False, server_default=""),
        sa.Column(
            "engagement_type",
            sa.String(32),
            nullable=False,
            server_default="like",
            index=True,
            comment="like, comment, repost, tip, save, bookmark, click, follow",
        ),
        sa.Column("protocol", sa.String(32), nullable=False, server_default="nostr", index=True),
        sa.Column("tip_amount_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("tip_token", sa.String(32), nullable=False, server_default=""),
        sa.Column("tip_amount_token", sa.Float, nullable=False, server_default="0"),
        sa.Column("comment_text", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "comment_sentiment",
            sa.String(16),
            nullable=False,
            server_default="neutral",
            comment="positive, negative, neutral, mixed",
        ),
        sa.Column("comment_sentiment_score", sa.Float, nullable=False, server_default="0", comment="-1.0 to 1.0"),
        sa.Column("clicked_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("click_position", sa.String(32), nullable=False, server_default=""),
        sa.Column("followed_after", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "external_engagement_id",
            sa.String(256),
            nullable=False,
            server_default="",
            comment="ID on source protocol for dedup",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_engagement_post", "engagement_events", ["post_id"])
    op.create_index("ix_engagement_creator", "engagement_events", ["creator_id"])
    op.create_index("ix_engagement_type", "engagement_events", ["engagement_type"])
    op.create_index("ix_engagement_creator_type", "engagement_events", ["creator_id", "engagement_type"])
    op.create_index("ix_engagement_protocol", "engagement_events", ["protocol"])

    # ------------------------------------------------------------------ #
    # audience_followers                                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        "audience_followers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("creator_profiles.id"), nullable=False),
        sa.Column("follower_fingerprint", sa.String(128), nullable=False, server_default="", index=True),
        sa.Column("follower_wallet", sa.String(256), nullable=False, server_default=""),
        sa.Column("follower_handle", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "protocol",
            sa.String(32),
            nullable=False,
            server_default="nostr",
            comment="nostr, farcaster, bluesky, mastodon",
        ),
        sa.Column("source_post_id", postgresql.UUID(as_uuid=True), nullable=True, comment="Post that drove the follow"),
        sa.Column("source_protocol", sa.String(32), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("unfollowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "interests",
            sa.String(2048),
            nullable=False,
            server_default="",
            comment="Comma-separated inferred interests",
        ),
        sa.Column(
            "chains_active",
            sa.String(512),
            nullable=False,
            server_default="",
            comment="Comma-separated chains they use",
        ),
        sa.Column("engagement_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_engagement_actions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_tips_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_follower_creator", "audience_followers", ["creator_id"])
    op.create_index("ix_follower_active", "audience_followers", ["creator_id", "is_active"])
    op.create_index("ix_follower_protocol", "audience_followers", ["creator_id", "protocol"])
    op.create_unique_constraint(
        "uq_follower_creator",
        "audience_followers",
        ["creator_id", "follower_fingerprint"],
    )


def downgrade() -> None:
    """Rollback analytics tables.

    WARNING: This DROPS all analytics data. Only run in dev/staging.
    Requires Lead approval for production.
    """
    op.drop_table("audience_followers")
    op.drop_table("engagement_events")
    op.drop_table("view_events")
    op.drop_table("content_posts")
    op.drop_table("creator_profiles")
