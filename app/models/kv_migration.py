"""KV-migration ORM models — persistent store for the DegenFeed KV replacement.

Phase 2 of the KV-to-selfhosted migration (Phase 1: app/routers/degenfeed_storage.py).
The degenfeed-api worker previously stored this data in Cloudflare KV. These models
back the persistent endpoints in app/api/v1/kv_migration.py:

  - IdentityMapping  → /api/v1/identities      (address → JSON blob)
  - Bookmark         → /api/v1/bookmarks       (address → post_id)
  - ChatMessage      → /api/v1/chat-history    (room-scoped chat messages)

Ephemeral data (rate limits, queues, sessions) intentionally stays in Redis and
has no ORM model — see app/api/v1/kv_migration.py.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.base import TimestampMixin


class IdentityMapping(Base, TimestampMixin):
    """User identity mapping (address → JSON blob).

    Replaces the worker's KV key ``identities/{address}``. ``data`` is a
    free-form JSON blob (e.g. protocol profiles, metadata) matching the
    original KV value semantics.
    """

    __tablename__ = "identity_mappings"
    __table_args__ = (UniqueConstraint("address", name="uq_identity_mappings_address"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    address: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )


class Bookmark(Base, TimestampMixin):
    """A saved post/bookmark for a wallet address.

    Replaces the worker's KV list ``bookmarks/{address}``. A (address, post_id)
    pair is unique — re-adding an existing bookmark is a no-op.
    """

    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("address", "post_id", name="uq_bookmarks_address_post"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    address: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    post_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )


class ChatMessage(Base, TimestampMixin):
    """A chat message in a room.

    Replaces the worker's KV list ``chat/{room_id}``. ``created_at`` is used
    for chronological ordering (oldest first) when fetching history.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    room_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    sender: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
