"""Audience analytics — growth, churn, sources."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

    def compute_audience_summary(
        self,
        followers: list[dict[str, Any]],
        views_by_follower: dict[str, list[dict[str, Any]]],
        engagements_by_follower: dict[str, list[dict[str, Any]]],
        period_days: int = 30,


    def compute_growth_analytics(
        self,
        followers: list[dict[str, Any]],
        period_days: int = 30,


