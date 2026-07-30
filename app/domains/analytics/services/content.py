"""Content insights — AI-driven pattern detection."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

    def compute_content_insights(
        self,
        posts: list[dict[str, Any]],
        views_by_post: dict[str, list[dict[str, Any]]],
        engagements_by_post: dict[str, list[dict[str, Any]]],


