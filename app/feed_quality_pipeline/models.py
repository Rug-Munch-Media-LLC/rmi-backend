"""
Feed quality data models — shared types used across all pipeline modules.

Exports:
    ProviderStatus, FeedPost, ProviderHealth, QualityMetrics
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    DOWN = "down"


@dataclass
class FeedPost:
    """Normalized feed post across all providers."""

    id: str
    title: str
    content: str
    source: str  # e.g. "web3isgoinggreat", "reddit", "twitter"
    provider_group: str  # e.g. "rss", "social", "news", "intel"
    url: str = ""
    author: str = ""
    published: str = ""
    score: float = 0.0
    quality_score: float = 0.0
    categories: list[str] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        """Unique content-based hash for dedup."""
        raw = f"{self.title}|{self.content[:500]}|{self.source}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def normalized_text(self) -> str:
        """Lowercased, stripped text for fuzzy comparison."""
        t = (self.title + " " + self.content)[:2000]
        return re.sub(r"\s+", " ", t).strip().lower()


@dataclass
class ProviderHealth:
    """Rolling health stats for a single provider."""

    name: str
    status: ProviderStatus = ProviderStatus.HEALTHY
    total_fetches: int = 0
    successful_fetches: int = 0
    failed_fetches: int = 0
    empty_responses: int = 0
    total_posts: int = 0
    avg_response_time_ms: float = 0.0
    last_fetch: str = ""
    last_error: str = ""
    circuit_open_until: float = 0.0
    consecutive_failures: int = 0
    recent_response_times: list[float] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)


@dataclass
class QualityMetrics:
    """Aggregated feed quality metrics."""

    total_providers: int = 0
    healthy_providers: int = 0
    degraded_providers: int = 0
    down_providers: int = 0
    total_posts: int = 0
    dedup_removed: int = 0
    validation_removed: int = 0
    avg_posts_per_fetch: float = 0.0
    avg_fetch_time_ms: float = 0.0
    empty_feed_rate: float = 0.0
    error_rate: float = 0.0
    cache_hit_rate: float = 0.0
    last_updated: str = ""


__all__ = [
    "ProviderStatus",
    "FeedPost",
    "ProviderHealth",
    "QualityMetrics",
]
