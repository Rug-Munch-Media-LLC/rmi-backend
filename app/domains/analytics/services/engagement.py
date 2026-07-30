"""Engagement analytics — likes, comments, tips."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

    def compute_engagement_analytics(
        self,
        engagements: list[dict[str, Any]],
        period_days: int = 7,


    def compute_cross_protocol_analytics(
        self,
        views_by_protocol: dict[str, list[dict[str, Any]]],
        engagements_by_protocol: dict[str, list[dict[str, Any]]],
        period_days: int = 7,


