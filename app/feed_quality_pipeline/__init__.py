"""
RMI Feed Quality Pipeline
=========================
Hardened feed pipeline with circuit breakers, cross-provider dedup,
content validation, scoring quality gates, and monitoring.

Usage:
    from app.feed_quality_pipeline import (
        FeedQualityPipeline, FeedPost, ContentValidator,
        FeedScorer, LRUCache, ProviderHealthMonitor, ...
    )
"""

from app.feed_quality_pipeline.cache import (
    CIRCUIT_BREAKER_RESET_SEC,
    CIRCUIT_BREAKER_THRESHOLD,
    DEDUP_SIMILARITY_THRESHOLD,
    HEALTH_WINDOW_MINUTES,
    LRU_CACHE_MAXSIZE,
    MAX_CONCURRENT_FETCHES,
    MAX_POST_LENGTH,
    MAX_POSTS_PER_PROVIDER,
    MAX_TITLE_LENGTH,
    MIN_POST_LENGTH,
    PROVIDER_TIMEOUT_DEFAULT,
    PROVIDER_TIMEOUT_SLOW,
    SEEN_IDS_MAXSIZE,
    LRUCache,
)
from app.feed_quality_pipeline.deduplicator import ContentDeduplicator
from app.feed_quality_pipeline.health import ProviderHealthMonitor
from app.feed_quality_pipeline.models import FeedPost, ProviderHealth, ProviderStatus, QualityMetrics
from app.feed_quality_pipeline.pipeline import FeedQualityPipeline, get_feed_pipeline, health_route
from app.feed_quality_pipeline.scorer import FeedScorer
from app.feed_quality_pipeline.validator import ContentValidator

__all__ = [
    # Constants
    "MAX_CONCURRENT_FETCHES",
    "PROVIDER_TIMEOUT_DEFAULT",
    "PROVIDER_TIMEOUT_SLOW",
    "CIRCUIT_BREAKER_THRESHOLD",
    "CIRCUIT_BREAKER_RESET_SEC",
    "LRU_CACHE_MAXSIZE",
    "DEDUP_SIMILARITY_THRESHOLD",
    "MAX_POST_LENGTH",
    "MIN_POST_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_POSTS_PER_PROVIDER",
    "SEEN_IDS_MAXSIZE",
    "HEALTH_WINDOW_MINUTES",
    # Enums
    "ProviderStatus",
    # Data classes
    "FeedPost",
    "ProviderHealth",
    "QualityMetrics",
    # Classes
    "LRUCache",
    "ContentValidator",
    "ContentDeduplicator",
    "FeedScorer",
    "ProviderHealthMonitor",
    "FeedQualityPipeline",
    # Functions
    "get_feed_pipeline",
    "health_route",
]
