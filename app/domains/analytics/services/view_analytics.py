"""View analytics — true counts, watch time, drop-off."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

    def compute_view_analytics(
        self,
        views: list[dict[str, Any]],
        period_days: int = 7,


