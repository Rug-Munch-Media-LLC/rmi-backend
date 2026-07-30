"""DEPRECATED — Import from app.domains.analytics.services instead.

This file is kept for backward compatibility. All functionality has been
moved to the `app.domains.analytics.services` package.
"""

from __future__ import annotations

from app.domains.analytics.services import (
    SECONDS_IN,
    SUPPORTED_PROTOCOLS,
    AnalyticsBuffer,
    CreatorAnalyticsEngine,
    aggregate_to_monthly,
    aggregate_to_weekly,
    avg_view_duration,
    compute_audience_summary,
    compute_content_insights,
    compute_cross_protocol_analytics,
    compute_engagement_analytics,
    compute_growth_analytics,
    compute_metric_by_group,
    compute_metric_by_hour,
    compute_protocol_peak_times,
    compute_view_analytics,
    confidence,
    empty_audience_summary,
    empty_engagement_summary,
    empty_growth,
    empty_view_summary,
    extract_top_category,
    get_analytics_engine,
    get_best_hour_str,
    hash_ip,
    infer_best_content_type,
)

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
