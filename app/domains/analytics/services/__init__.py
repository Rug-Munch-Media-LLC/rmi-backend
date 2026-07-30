"""Analytics services — buffer, engine, compute modules."""

from __future__ import annotations

from app.domains.analytics.services.audience import (
    compute_audience_summary,
    compute_growth_analytics,
)
from app.domains.analytics.services.buffer import AnalyticsBuffer
from app.domains.analytics.services.constants import SECONDS_IN, SUPPORTED_PROTOCOLS
from app.domains.analytics.services.content import compute_content_insights
from app.domains.analytics.services.engagement import (
    compute_cross_protocol_analytics,
    compute_engagement_analytics,
)
from app.domains.analytics.services.engine import CreatorAnalyticsEngine, get_analytics_engine
from app.domains.analytics.services.helpers import (
    aggregate_to_monthly,
    aggregate_to_weekly,
    avg_view_duration,
    compute_metric_by_group,
    compute_metric_by_hour,
    compute_protocol_peak_times,
    confidence,
    empty_audience_summary,
    empty_engagement_summary,
    empty_growth,
    empty_view_summary,
    extract_top_category,
    get_best_hour_str,
    hash_ip,
    infer_best_content_type,
)
from app.domains.analytics.services.view_analytics import compute_view_analytics

__all__ = [
    "SECONDS_IN",
    "SUPPORTED_PROTOCOLS",
    "AnalyticsBuffer",
    "CreatorAnalyticsEngine",
    "aggregate_to_monthly",
    "aggregate_to_weekly",
    "avg_view_duration",
    "compute_audience_summary",
    "compute_content_insights",
    "compute_cross_protocol_analytics",
    "compute_engagement_analytics",
    "compute_growth_analytics",
    "compute_metric_by_group",
    "compute_metric_by_hour",
    "compute_protocol_peak_times",
    "compute_view_analytics",
    "confidence",
    "empty_audience_summary",
    "empty_engagement_summary",
    "empty_growth",
    "empty_view_summary",
    "extract_top_category",
    "get_analytics_engine",
    "get_best_hour_str",
    "hash_ip",
    "infer_best_content_type",
]
