"""AnalyticsBuffer — in-memory event aggregation buffer."""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

class AnalyticsBuffer:
    """Thread-safe in-memory buffer for real-time event ingestion.

    Buffers view and engagement events in memory and flushes to Postgres
    periodically.  This gives us sub-second write latency without hammering
    the database.
    """

