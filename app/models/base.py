"""Shared ORM mixins and utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

if TYPE_CHECKING:
    from datetime import datetime


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
