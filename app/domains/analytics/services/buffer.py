"""AnalyticsBuffer — in-memory event aggregation buffer for real-time ingestion."""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class AnalyticsBuffer:
    """Thread-safe in-memory buffer for real-time event ingestion.

    Buffers view and engagement events in memory and flushes to Postgres
    periodically.  This gives us sub-second write latency without hammering
    the database.
    """

    def __init__(self, flush_interval_seconds: int = 5, max_batch_size: int = 500):
        self._view_buffer: list[dict[str, Any]] = []
        self._engagement_buffer: list[dict[str, Any]] = []
        self._follower_buffer: list[dict[str, Any]] = []
        self._flush_interval = flush_interval_seconds
        self._max_batch_size = max_batch_size
        self._last_flush = time.monotonic()

    def add_view(self, event: dict[str, Any]) -> None:
        """Buffer a view event for batch insert."""
        self._view_buffer.append(event)

    def add_engagement(self, event: dict[str, Any]) -> None:
        """Buffer an engagement event for batch insert."""
        self._engagement_buffer.append(event)

    def add_follower(self, event: dict[str, Any]) -> None:
        """Buffer a follower event for batch insert."""
        self._follower_buffer.append(event)

    def should_flush(self) -> bool:
        """Check if buffer should be flushed."""
        elapsed = time.monotonic() - self._last_flush
        total = len(self._view_buffer) + len(self._engagement_buffer) + len(self._follower_buffer)
        return elapsed >= self._flush_interval or total >= self._max_batch_size

    def drain(self) -> dict[str, list[dict[str, Any]]]:
        """Drain all buffered events and return them."""
        data = {
            "views": self._view_buffer,
            "engagements": self._engagement_buffer,
            "followers": self._follower_buffer,
        }
        self._view_buffer = []
        self._engagement_buffer = []
        self._follower_buffer = []
        self._last_flush = time.monotonic()
        return data
