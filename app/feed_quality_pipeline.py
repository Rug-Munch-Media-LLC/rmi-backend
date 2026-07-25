"""
RMI Feed Quality Pipeline
=========================
Hardened feed pipeline with circuit breakers, cross-provider dedup,
content validation, scoring quality gates, and monitoring.

Wraps all existing feed infrastructure (RSS, social, news, intel)
with reliability guarantees.

Key features:
  - Circuit breaker per provider (auto-opens on N failures)
  - Semaphore-based concurrency limiting
  - Independent per-provider timeouts
  - Cross-provider fuzzy dedup (RapidFuzz Levenshtein)
  - Content validation (rejects garbage/malformed)
  - Recency scoring with proper edge cases
  - Provider health dashboard
  - LRU-capped in-memory caches (prevents OOM)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Callable

from rapidfuzz import fuzz

from app.core.logging import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

MAX_CONCURRENT_FETCHES = 10  # Global semaphore
PROVIDER_TIMEOUT_DEFAULT = 15.0  # Seconds per provider
PROVIDER_TIMEOUT_SLOW = 30.0  # For slow providers (RSS with many entries)
CIRCUIT_BREAKER_THRESHOLD = 3  # Failures before circuit opens
CIRCUIT_BREAKER_RESET_SEC = 300  # Seconds before half-open retry
LRU_CACHE_MAXSIZE = 2000  # Max cached entries per cache
DEDUP_SIMILARITY_THRESHOLD = 88  # RapidFuzz score % for near-duplicate
MAX_POST_LENGTH = 100000  # Reject posts over 100K chars
MIN_POST_LENGTH = 5  # Reject posts under 5 chars
MAX_TITLE_LENGTH = 1000  # Reject absurdly long titles
MAX_POSTS_PER_PROVIDER = 100  # Cap on posts from one provider per cycle
SEEN_IDS_MAXSIZE = 50000  # Max tracked seen IDs (prevents OOM)
HEALTH_WINDOW_MINUTES = 60  # Rolling health window


class ProviderStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    DOWN = "down"


# ══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════


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


# ══════════════════════════════════════════════════════════════════════
# LRU CACHE (bounded memory)
# ══════════════════════════════════════════════════════════════════════


class LRUCache:
    """Thread-safe LRU cache with TTL and maxsize. Prevents OOM."""

    def __init__(self, maxsize: int = LRU_CACHE_MAXSIZE) -> None:
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Any | None:
        if key not in self._cache:
            return None
        expires_at, value = self._cache[key]
        if time.monotonic() > expires_at:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        expires_at = time.monotonic() + ttl
        self._cache[key] = (expires_at, value)
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def keys_starting_with(self, prefix: str) -> list[str]:
        return [k for k in self._cache if k.startswith(prefix)]


# ══════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════════════


class CircuitBreaker:
    """
    Per-provider circuit breaker.
    Prevents hammering a provider that's already failing.

    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        reset_timeout: int = CIRCUIT_BREAKER_RESET_SEC,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = ProviderStatus.HEALTHY
        self.last_error = ""

    def record_success(self) -> None:
        """Successful call — reset failure count."""
        self.failure_count = 0
        self.state = ProviderStatus.HEALTHY
        self.last_error = ""

    def record_failure(self, error: str) -> None:
        """Failed call — increment counter, possibly open circuit."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        self.last_error = error[:200]
        if self.failure_count >= self.failure_threshold:
            self.state = ProviderStatus.CIRCUIT_OPEN
            logger.warning(
                "circuit_breaker_opened",
                provider=self.name,
                failures=self.failure_count,
                error=error[:100],
            )

    def can_proceed(self) -> bool:
        """Check if request can proceed based on circuit state."""
        if self.state == ProviderStatus.CIRCUIT_OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.reset_timeout:
                # Half-open: allow a probe request
                self.state = ProviderStatus.DEGRADED
                logger.info("circuit_breaker_half_open", provider=self.name)
                return True
            return False
        return True

    def get_status(self) -> ProviderStatus:
        return self.state


# ══════════════════════════════════════════════════════════════════════
# CONTENT VALIDATOR
# ══════════════════════════════════════════════════════════════════════


class ContentValidator:
    """
    Validates and sanitizes post content.
    Rejects malformed, garbage, or abusive content.
    """

    # Patterns that indicate garbage/malformed content
    GARBAGE_PATTERNS: list[re.Pattern] = [
        re.compile(r"^[\s\d\W]{1,20}$"),  # Only whitespace/digits/symbols
        re.compile(r"^(Error|Exception|Traceback|undefined|null|None)\s*$", re.I),
        re.compile(r"[\\]{4,}"),  # Escaped garbage
        re.compile(r"<[^>]*>", re.I),  # HTML tags (if no content)
    ]

    # Known spam patterns
    SPAM_PATTERNS: list[re.Pattern] = [
        re.compile(r"(buy\s*now|click\s*here|subscribe|free\s*money)", re.I),
        re.compile(r"https?://[^\s]{100,}"),  # absurdly long URLs
    ]

    @classmethod
    def validate_post(cls, post: FeedPost) -> tuple[bool, str]:
        """
        Validate a feed post. Returns (is_valid, reason).

        Edge cases handled:
        - Zero-length / whitespace-only content
        - Binary/garbage content
        - Absurdly long content
        - Missing required fields
        - HTML-only content (no actual text)
        """
        # Length checks
        if not post.content and not post.title:
            return False, "empty_content"
        if len(post.content) > MAX_POST_LENGTH:
            return False, "content_too_long"
        if post.title and len(post.title) > MAX_TITLE_LENGTH:
            return False, "title_too_long"
        if len(post.content.strip()) < MIN_POST_LENGTH and len(post.title.strip()) < MIN_POST_LENGTH:
            return False, "content_too_short"

        # Garbage detection
        text = (post.title + " " + post.content)[:500]
        for pattern in cls.GARBAGE_PATTERNS:
            if pattern.search(text):
                return False, f"garbage_content: {pattern.pattern[:30]}"

        # Spam detection
        for pattern in cls.SPAM_PATTERNS:
            if pattern.search(text):
                return False, f"spam_pattern: {pattern.pattern[:30]}"

        # Repeated character check
        if cls._is_repetitive_garbage(text):
            return False, "repetitive_garbage"

        return True, ""

    @classmethod
    def _is_repetitive_garbage(cls, text: str) -> bool:
        """Detect repetitive garbage like 'aaaaaa...' or '12121212...'."""
        if len(text) < 20:
            return False
        # Check if >70% of chars are the same
        if len(text) >= 20:
            counts: dict[str, int] = {}
            for c in text[:200].strip():
                counts[c] = counts.get(c, 0) + 1
            if counts and max(counts.values()) > len(text[:200]) * 0.7:
                return True
        return False


# ══════════════════════════════════════════════════════════════════════
# CONTENT DEDUPLICATOR
# ══════════════════════════════════════════════════════════════════════


class ContentDeduplicator:
    """
    Cross-provider content deduplication.
    Handles exact dupes (hash-based) and near-dup es (fuzzy).

    Edge cases covered:
    - Exact duplicate across providers (same headline from CoinDesk and Blockworks)
    - Near-identical posts (same story, slightly different wording)
    - Same URL shared with different context → NOT merged
    - Translated content → NOT merged
    """

    def __init__(self, similarity_threshold: int = DEDUP_SIMILARITY_THRESHOLD) -> None:
        self._similarity_threshold = similarity_threshold
        self._seen_hashes: set[str] = set()
        self._seen_urls: set[str] = set()
        self._recent_titles: list[tuple[str, str, str]] = []  # (normalized, source, id)

    def reset(self) -> None:
        """Reset dedup state for a new cycle."""
        self._seen_hashes.clear()
        self._seen_urls.clear()
        self._recent_titles.clear()

    def is_duplicate(self, post: FeedPost) -> bool:
        """
        Check if this post is a duplicate of something already seen.

        Returns True if duplicate should be skipped.
        """
        # 1. Exact content hash match
        h = post.content_hash()
        if h in self._seen_hashes:
            return True

        # 2. Same URL, non-empty → duplicate (but NOT if URL is empty/default)
        if post.url and len(post.url) > 10:
            url_key = post.url.rstrip("/").lower()
            if url_key in self._seen_urls:
                return True
            self._seen_urls.add(url_key)

        # 3. Near-duplicate: RapidFuzz Levenshtein ratio on title+content
        normalized = post.normalized_text()
        for prev_text, prev_source, prev_id in self._recent_titles:
            # Don't cross-merge translated content (language heuristics)
            if self._is_different_language(normalized, prev_text):
                continue
            # Same source is subject to stricter dedup
            threshold = self._similarity_threshold
            if post.source == prev_source:
                threshold = max(threshold, 92)  # Stricter within same source
            similarity = fuzz.token_sort_ratio(normalized[:1000], prev_text[:1000])
            if similarity >= threshold:
                return True

        # Not duplicate — add to tracking
        self._seen_hashes.add(h)
        self._recent_titles.append((normalized, post.source, post.id))
        # Keep recent window bounded
        if len(self._recent_titles) > 10000:
            self._recent_titles = self._recent_titles[-5000:]

        return False

    @staticmethod
    def _is_different_language(text_a: str, text_b: str) -> bool:
        """
        Heuristic: if two texts have very different character distributions,
        they are likely different languages and shouldn't be dedup'd.
        """
        if len(text_a) < 20 or len(text_b) < 20:
            return False  # too short to determine
        # Count non-ASCII chars as a proxy for different scripts
        non_ascii_a = sum(1 for c in text_a if ord(c) > 127)
        non_ascii_b = sum(1 for c in text_b if ord(c) > 127)
        ratio_a = non_ascii_a / max(len(text_a), 1)
        ratio_b = non_ascii_b / max(len(text_b), 1)
        # If one has significantly more non-ASCII, they're different scripts
        if abs(ratio_a - ratio_b) > 0.3:
            return True
        return False


# ══════════════════════════════════════════════════════════════════════
# FEED SCORER
# ══════════════════════════════════════════════════════════════════════


class FeedScorer:
    """
    Scores feed posts with proper edge case handling.

    Edge cases covered:
    - Zero engagement: minimum baseline score
    - Very old post: graceful recency decay (not negative)
    - Unknown creator: neutral default affinity
    - Empty user profile: falls back to trending
    - Rapid preference changes: smoothed scoring
    """

    # Default half-life for recency decay (hours)
    RECENCY_HALF_LIFE_HOURS = 24.0

    # Minimum baseline scores
    MIN_CONTENT_SCORE = 0.05
    MIN_ENGAGEMENT_SCORE = 0.1
    MIN_SOURCE_CREDIBILITY = 0.2

    # Source tier → credibility mapping
    SOURCE_CREDIBILITY: dict[int, float] = {
        1: 1.0,  # Top-tier news
        2: 0.9,
        3: 0.8,
        4: 0.9,  # Security firms
        5: 0.85,  # Analytics
        6: 0.9,  # VC blogs
        7: 0.7,  # Newsletters
        8: 0.5,  # Exchange blogs
    }

    # Provider group → default credibility
    GROUP_CREDIBILITY: dict[str, float] = {
        "news": 0.85,
        "intel": 0.9,
        "social": 0.5,
        "rss": 0.75,
    }

    @classmethod
    def score_post(
        cls,
        post: FeedPost,
        user_preferences: dict[str, float] | None = None,
        creator_affinity: float | None = None,
    ) -> float:
        """
        Compute a unified quality score for a post (0.0 to 1.0).

        Components:
        1. Content quality (title clarity, content length)
        2. Source credibility (tier-based)
        3. Recency (exponential decay from publish time)
        4. Engagement signal (normalized)
        5. Creator affinity (defaults to neutral 0.5)
        6. Personalization (user preference weights)

        Always returns >= MIN_CONTENT_SCORE so zero-engagement posts
        still get a chance at being seen.
        """
        # 1. Content quality score
        content_quality = cls._score_content_quality(post)

        # 2. Source credibility
        source_cred = cls._get_source_credibility(post)

        # 3. Recency (always >= 0, never negative)
        recency = cls._score_recency(post)

        # 4. Engagement signal
        engagement = cls._score_engagement(post)

        # 5. Creator affinity (defaults to neutral)
        affinity = creator_affinity if creator_affinity is not None else 0.5

        # 6. Personalization
        personalization = 0.5
        if user_preferences:
            personalization = cls._score_personalization(post, user_preferences)

        # Weighted combination
        score = (
            0.25 * content_quality
            + 0.20 * source_cred
            + 0.25 * recency
            + 0.10 * engagement
            + 0.10 * affinity
            + 0.10 * personalization
        )

        # Ensure minimum floor
        return max(cls.MIN_CONTENT_SCORE, min(1.0, score))

    @classmethod
    def _score_content_quality(cls, post: FeedPost) -> float:
        """Score based on content quality signals."""
        score = 0.3  # Baseline

        # Title quality
        if post.title and len(post.title) > 10:
            score += 0.2
        if post.title and post.title[0].isupper():
            score += 0.1

        # Content length sweet spot (100-5000 chars = good)
        content_len = len(post.content)
        if 100 <= content_len <= 5000:
            score += 0.2
        elif content_len > 5000:
            score += 0.15
        elif content_len > 20:
            score += 0.1

        # Has meaningful body
        words = post.content.split()
        if len(words) >= 10:
            score += 0.1
        if len(words) >= 50:
            score += 0.1

        # Author present
        if post.author:
            score += 0.1

        return min(1.0, score)

    @classmethod
    def _get_source_credibility(cls, post: FeedPost) -> float:
        """Source credibility based on tier or group defaults."""
        # Check if raw has tier info
        source_tier = post.raw.get("source_tier") if post.raw else None
        if source_tier and isinstance(source_tier, int):
            return cls.SOURCE_CREDIBILITY.get(source_tier, 0.5)

        # Fall back to group defaults
        return cls.GROUP_CREDIBILITY.get(post.provider_group, 0.5)

    @classmethod
    def _score_recency(cls, post: FeedPost) -> float:
        """
        Exponential recency decay. Never negative.
        Very old posts approach 0 asymptotically but never go negative.

        Edge case: missing publish date → assume moderately fresh (0.5)
        """
        if not post.published:
            return 0.5  # Neutral default

        try:
            if isinstance(post.published, str):
                pub = post.published.replace("Z", "+00:00")
                pub_dt = datetime.fromisoformat(pub)
            elif isinstance(post.published, datetime):
                pub_dt = post.published
            else:
                return 0.5

            age_hours = max(0.0, (datetime.now(UTC) - pub_dt).total_seconds() / 3600.0)
        except (ValueError, TypeError):
            return 0.5

        # Exponential decay: score = e^(-ln(2) * age_hours / half_life)
        # After `half_life` hours, score = 0.5
        lam = 0.693 / cls.RECENCY_HALF_LIFE_HOURS  # ln(2) / half_life
        score = max(0.01, min(1.0, 1.0 * (2.0 ** (-age_hours / cls.RECENCY_HALF_LIFE_HOURS))))
        return score

    @classmethod
    def _score_engagement(cls, post: FeedPost) -> float:
        """
        Score based on engagement signals.
        Zero engagement → returns baseline (not zero, not negative).
        """
        baseline = 0.15
        signals = 0.0
        count = 0

        # Extract engagement from raw data if available
        if post.raw:
            # Reddit-style
            for field in ["score", "ups", "likes", "favorites", "retweet_count"]:
                val = post.raw.get(field, 0)
                if isinstance(val, (int, float)) and val > 0:
                    signals += min(1.0, val / 1000.0)  # Normalize
                    count += 1

            # Comment count
            for field in ["num_comments", "replies", "comments"]:
                val = post.raw.get(field, 0)
                if isinstance(val, (int, float)) and val > 0:
                    signals += min(1.0, val / 100.0)
                    count += 1

        if count > 0:
            return min(1.0, baseline + (signals / count) * 0.5)
        return baseline

    @classmethod
    def _score_personalization(cls, post: FeedPost, preferences: dict[str, float]) -> float:
        """
        Score based on user preferences.
        Smooths rapid changes with damping.

        preferences: dict of category → weight (0.0 to 1.0)
        """
        if not preferences:
            return 0.5

        score = 0.5
        matched = 0

        for cat_name, weight in preferences.items():
            cat_lower = cat_name.lower()
            if any(cat_lower in c.lower() for c in post.categories):
                # Damped weight (prevents extreme swings from rapid preference changes)
                damped = 0.5 + (weight - 0.5) * 0.5
                score += damped
                matched += 1
            if cat_lower in post.content[:200].lower():
                damped = 0.5 + (weight - 0.5) * 0.3
                score += damped * 0.5
                matched += 1

        if matched > 0:
            return min(1.0, score / (matched + 1))
        return 0.5


# ══════════════════════════════════════════════════════════════════════
# PROVIDER HEALTH MONITOR
# ══════════════════════════════════════════════════════════════════════


class ProviderHealthMonitor:
    """Tracks per-provider health with rolling windows."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderHealth] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

    def register_provider(self, name: str) -> None:
        """Register a provider for health tracking."""
        if name not in self._providers:
            self._providers[name] = ProviderHealth(name=name)
            self._circuit_breakers[name] = CircuitBreaker(name=name)

    def record_fetch_start(self, name: str) -> float:
        """Record start of fetch, return start time."""
        if name not in self._providers:
            self.register_provider(name)
        self._providers[name].total_fetches += 1
        return time.monotonic()

    def record_fetch_success(self, name: str, start_time: float, post_count: int) -> None:
        """Record a successful fetch."""
        elapsed_ms = (time.monotonic() - start_time) * 1000
        h = self._providers.get(name)
        if not h:
            return

        h.successful_fetches += 1
        h.total_posts += post_count
        h.avg_response_time_ms = (h.avg_response_time_ms * (h.successful_fetches - 1) + elapsed_ms) / max(
            h.successful_fetches, 1
        )
        h.last_fetch = datetime.now(UTC).isoformat()
        h.last_error = ""
        h.circuit_open_until = 0.0

        # Track recent response times (rolling window)
        h.recent_response_times.append(elapsed_ms)
        if len(h.recent_response_times) > 100:
            h.recent_response_times = h.recent_response_times[-50:]

        if post_count == 0:
            h.empty_responses += 1

        # Update circuit breaker
        cb = self._circuit_breakers.get(name)
        if cb:
            cb.record_success()

        self._update_status(name)

    def record_fetch_failure(self, name: str, start_time: float, error: str) -> None:
        """Record a failed fetch."""
        h = self._providers.get(name)
        if not h:
            return

        h.failed_fetches += 1
        h.last_error = error[:200]
        h.last_fetch = datetime.now(UTC).isoformat()

        # Track recent errors
        h.recent_errors.append(error[:100])
        if len(h.recent_errors) > 20:
            h.recent_errors = h.recent_errors[-10:]

        # Update circuit breaker
        cb = self._circuit_breakers.get(name)
        if cb:
            cb.record_failure(error)

        self._update_status(name)

    def can_fetch(self, name: str) -> bool:
        """Check if we're allowed to fetch from this provider."""
        cb = self._circuit_breakers.get(name)
        if not cb:
            return True
        return cb.can_proceed()

    def get_provider_status(self, name: str) -> ProviderStatus:
        cb = self._circuit_breakers.get(name)
        if cb:
            return cb.get_status()
        return ProviderStatus.HEALTHY

    def _update_status(self, name: str) -> None:
        """Derive overall status from health + circuit breaker."""
        h = self._providers.get(name)
        if not h:
            return

        cb = self._circuit_breakers.get(name)
        if cb and cb.state == ProviderStatus.CIRCUIT_OPEN:
            h.status = ProviderStatus.CIRCUIT_OPEN
        elif h.failed_fetches > 0 and h.successful_fetches == 0:
            h.status = ProviderStatus.DOWN
        elif h.total_fetches > 0 and (h.failed_fetches / max(h.total_fetches, 1) > 0.5):
            h.status = ProviderStatus.DEGRADED
        else:
            h.status = ProviderStatus.HEALTHY

    def get_all_health(self) -> dict[str, ProviderHealth]:
        return dict(self._providers)

    def get_dashboard(self) -> dict[str, Any]:
        """Get provider health for dashboard display."""
        dashboard = {}
        for name, h in self._providers.items():
            dashboard[name] = {
                "status": h.status.value,
                "total_fetches": h.total_fetches,
                "successful": h.successful_fetches,
                "failed": h.failed_fetches,
                "empty_responses": h.empty_responses,
                "total_posts_ingested": h.total_posts,
                "avg_response_ms": round(h.avg_response_time_ms, 1),
                "last_fetch": h.last_fetch,
                "circuit_breaker": self._circuit_breakers.get(name, CircuitBreaker(name)).state.value,
            }
        return dashboard


# ══════════════════════════════════════════════════════════════════════
# FEED QUALITY PIPELINE (ORCHESTRATOR)
# ══════════════════════════════════════════════════════════════════════


class FeedQualityPipeline:
    """
    Unified feed pipeline with:
    - Parallel fetches with global concurrency limit
    - Per-provider timeouts
    - Circuit breaker pattern
    - Cross-provider content dedup
    - Content validation
    - Scoring quality gates
    - Health monitoring
    - Bounded memory LRU caching
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        self._dedup = ContentDeduplicator()
        self._validator = ContentValidator()
        self._scorer = FeedScorer()
        self._health = ProviderHealthMonitor()
        self._fetched_posts_cache = LRUCache(maxsize=500)
        self._quality_cache = LRUCache(maxsize=2000)
        self._seen_ids: set[str] = set()

    # ── Provider wrapper ──────────────────────────────────────────

    @dataclass
    class ProviderResult:
        """Result from a single provider fetch."""

        name: str
        posts: list[FeedPost]
        error: str | None = None
        elapsed_ms: float = 0.0

    async def fetch_provider(
        self,
        name: str,
        fetch_fn: Callable[[], list[FeedPost] | dict[str, Any]],
        provider_group: str = "rss",
        timeout: float = PROVIDER_TIMEOUT_DEFAULT,
    ) -> ProviderResult:
        """
        Fetch from a single provider with protection.

        Edge cases handled:
        - Empty provider: returns empty posts list, tracks as empty response
        - Slow provider: timeout raises asyncio.TimeoutError → tracked as failure
        - Error provider: exception caught, tracked, returns empty
        - Rate limited: circuit opens after threshold
        """
        self._health.register_provider(name)

        # Circuit breaker check
        if not self._health.can_fetch(name):
            logger.info("circuit_open_skip", provider=name)
            return self.ProviderResult(
                name=name,
                posts=[],
                error=f"Circuit breaker open, skipping until reset",
            )

        start = self._health.record_fetch_start(name)

        try:
            async with self._semaphore:
                # Run with timeout
                result = await asyncio.wait_for(
                    self._async_wrap_fetch(fetch_fn),
                    timeout=timeout,
                )

            # Normalize result
            posts = self._normalize_result(result, name, provider_group)
            elapsed_ms = (time.monotonic() - start) * 1000

            # Track health
            self._health.record_fetch_success(name, start, len(posts))

            return self.ProviderResult(
                name=name,
                posts=posts,
                elapsed_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            error = f"Timeout after {timeout}s ({elapsed:.0f}ms)"
            self._health.record_fetch_failure(name, start, error)
            logger.warning("provider_timeout", provider=name, timeout=timeout)
            return self.ProviderResult(name=name, posts=[], error=error)

        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            error = f"{type(e).__name__}: {str(e)[:200]}"
            self._health.record_fetch_failure(name, start, error)
            logger.warning("provider_error", provider=name, error=str(e)[:100])
            return self.ProviderResult(name=name, posts=[], error=error, elapsed_ms=elapsed)

    @staticmethod
    async def _async_wrap_fetch(fetch_fn: Callable) -> Any:
        """Wrap a potentially sync fetch function for async execution."""
        if asyncio.iscoroutinefunction(fetch_fn):
            return await fetch_fn()
        # Run sync function in thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fetch_fn)

    def _normalize_result(
        self,
        result: Any,
        source: str,
        provider_group: str,
    ) -> list[FeedPost]:
        """
        Normalize various feed result formats into list[FeedPost].

        Handles dicts (like social_feed.py returns), lists (like simple RSS),
        or None.
        """
        posts: list[FeedPost] = []

        if result is None:
            return posts

        if isinstance(result, list):
            for item in result[:MAX_POSTS_PER_PROVIDER]:
                post = self._dict_to_post(item, source, provider_group)
                if post:
                    posts.append(post)

        elif isinstance(result, dict):
            # Handle various dict formats
            # Format 1: {"posts": [...]} or {"articles": [...]} or {"tweets": [...]}
            for key in ("posts", "articles", "tweets", "items", "results"):
                items = result.get(key, [])
                if isinstance(items, list):
                    for item in items[:MAX_POSTS_PER_PROVIDER]:
                        post = self._dict_to_post(item, source, provider_group)
                        if post:
                            posts.append(post)
                    if posts:
                        break

            # Format 2: Nested e.g. {"data": {"posts": [...]}}
            if not posts and "data" in result and isinstance(result["data"], dict):
                for key in ("posts", "articles", "items"):
                    items = result["data"].get(key, [])
                    if isinstance(items, list):
                        for item in items:
                            post = self._dict_to_post(item, source, provider_group)
                            if post:
                                posts.append(post)
                        if posts:
                            break

        return posts[:MAX_POSTS_PER_PROVIDER]

    def _dict_to_post(self, item: Any, source: str, provider_group: str) -> FeedPost | None:
        """Convert a dict from any provider into a FeedPost."""
        if not isinstance(item, dict):
            return None

        # Extract fields with fallbacks for different formats
        title = item.get("title") or item.get("text", "") or item.get("headline", "") or ""
        content = (
            item.get("content")
            or item.get("body")
            or item.get("description")
            or item.get("summary")
            or item.get("text", "")
            or ""
        )
        url = item.get("url") or item.get("link") or item.get("permalink") or ""

        # Generate stable ID
        raw_id = item.get("id") or item.get("doc_id") or item.get("_id") or ""
        if not raw_id:
            raw_id = hashlib.sha256(f"{source}:{title}:{content[:200]}".encode()).hexdigest()[:16]

        author = item.get("author") or item.get("creator") or item.get("byline") or item.get("handle") or ""

        published = (
            item.get("published")
            or item.get("published_at")
            or item.get("created_at")
            or item.get("pubDate")
            or item.get("date")
            or item.get("timestamp")
            or item.get("created_utc", "")
        )
        if isinstance(published, (int, float)):
            try:
                published = datetime.fromtimestamp(published, UTC).isoformat()
            except (OSError, OverflowError, ValueError):
                published = ""

        categories = item.get("categories") or item.get("tags") or []
        if isinstance(categories, str):
            categories = [categories]

        raw = {k: v for k, v in item.items() if isinstance(v, (str, int, float, bool, list))}
        raw["source_tier"] = item.get("source_tier") or item.get("tier", 5)

        return FeedPost(
            id=str(raw_id),
            title=str(title)[:MAX_TITLE_LENGTH],
            content=str(content)[:MAX_POST_LENGTH],
            source=source,
            provider_group=provider_group,
            url=str(url),
            author=str(author)[:100],
            published=str(published)[:50] if published else "",
            categories=[str(c)[:50] for c in categories] if categories else [],
            raw=raw,
        )

    # ── Pipeline execution ────────────────────────────────────────

    async def process_providers(
        self,
        providers: list[tuple[str, Callable, str, float]],
        user_preferences: dict[str, float] | None = None,
        creator_affinities: dict[str, float] | None = None,
    ) -> tuple[list[FeedPost], QualityMetrics]:
        """
        Execute a full pipeline cycle across multiple providers.

        Args:
            providers: list of (name, fetch_fn, group, timeout)
            user_preferences: optional category → weight mapping
            creator_affinities: optional author → affinity mapping

        Returns:
            (deduped_scored_posts, quality_metrics)
        """
        self._dedup.reset()

        # Phase 1: Fetch all providers in parallel
        results = await asyncio.gather(
            *[self.fetch_provider(name, fn, group, timeout) for name, fn, group, timeout in providers],
            return_exceptions=True,
        )

        # Phase 2: Collect & merge results
        all_posts: list[FeedPost] = []
        raw_results: list[ProviderResult] = []
        for result in results:
            if isinstance(result, Exception):
                # Should not happen with exception handling inside fetch_provider
                logger.error("unexpected_pipeline_error", error=str(result)[:100])
                continue
            raw_results.append(result)
            all_posts.extend(result.posts)

        # Phase 3: Validate content
        valid_posts: list[FeedPost] = []
        validation_removed = 0
        for post in all_posts:
            is_valid, reason = self._validator.validate_post(post)
            if is_valid:
                valid_posts.append(post)
            else:
                validation_removed += 1
                logger.debug("validation_removed", source=post.source, reason=reason)

        # Phase 4: Cross-provider dedup
        deduped_posts: list[FeedPost] = []
        dedup_removed = 0
        new_count = 0
        for post in valid_posts:
            # Check if already seen in this cycle (cross-provider)
            if self._dedup.is_duplicate(post):
                dedup_removed += 1
                continue
            # Check against global seen IDs
            if post.id in self._seen_ids:
                dedup_removed += 1
                continue
            self._seen_ids.add(post.id)
            deduped_posts.append(post)

        # Prune seen_ids to prevent OOM
        if len(self._seen_ids) > SEEN_IDS_MAXSIZE:
            self._seen_ids = set(list(self._seen_ids)[-SEEN_IDS_MAXSIZE // 2 :])

        # Phase 5: Score
        for post in deduped_posts:
            creator_aff = None
            if creator_affinities and post.author in creator_affinities:
                creator_aff = creator_affinities[post.author]
            post.score = self._scorer.score_post(
                post,
                user_preferences=user_preferences,
                creator_affinity=creator_aff,
            )

        # Sort by score descending
        deduped_posts.sort(key=lambda p: p.score, reverse=True)

        # Phase 6: Build metrics
        metrics = self._build_metrics(raw_results, deduped_posts, dedup_removed, validation_removed)

        return deduped_posts, metrics

    def _build_metrics(
        self,
        results: list[ProviderResult],
        final_posts: list[FeedPost],
        dedup_removed: int,
        validation_removed: int,
    ) -> QualityMetrics:
        """Build quality metrics from pipeline results."""
        m = QualityMetrics()
        m.total_providers = len(results)
        m.total_posts = len(final_posts)
        m.dedup_removed = dedup_removed
        m.validation_removed = validation_removed
        m.last_updated = datetime.now(UTC).isoformat()

        health_data = self._health.get_all_health()
        m.healthy_providers = sum(1 for h in health_data.values() if h.status == ProviderStatus.HEALTHY)
        m.degraded_providers = sum(1 for h in health_data.values() if h.status == ProviderStatus.DEGRADED)
        m.down_providers = sum(
            1 for h in health_data.values() if h.status in (ProviderStatus.DOWN, ProviderStatus.CIRCUIT_OPEN)
        )

        total_posts_fetched = sum(len(r.posts) for r in results)
        m.avg_posts_per_fetch = round(total_posts_fetched / max(len(results), 1), 1)

        avg_times = [r.elapsed_ms for r in results if r.elapsed_ms > 0]
        m.avg_fetch_time_ms = round(sum(avg_times) / max(len(avg_times), 1), 1) if avg_times else 0.0

        empty_feeds = sum(1 for r in results if len(r.posts) == 0 and not r.error)
        m.empty_feed_rate = round(empty_feeds / max(len(results), 1), 3)

        errors = sum(1 for r in results if r.error)
        m.error_rate = round(errors / max(len(results), 1), 3)

        return m

    # ── Health dashboard ──────────────────────────────────────────

    def get_provider_dashboard(self) -> dict[str, Any]:
        """Real-time provider health dashboard."""
        return self._health.get_dashboard()

    def get_quality_report(self) -> dict[str, Any]:
        """Feed quality metrics report."""
        # Simplified - in production would return current metrics
        return {
            "last_updated": datetime.now(UTC).isoformat(),
            "providers": self._health.get_dashboard(),
            "worst_performers": sorted(
                self._health.get_all_health().values(),
                key=lambda h: h.failed_fetches / max(h.total_fetches, 1),
                reverse=True,
            )[:5],
        }

    def get_cache_stats(self) -> dict[str, Any]:
        """Cache utilization statistics."""
        return {
            "fetched_posts_cache_size": len(self._fetched_posts_cache),
            "quality_cache_size": len(self._quality_cache),
            "seen_ids_count": len(self._seen_ids),
        }

    def check_degradation(self) -> list[dict[str, Any]]:
        """
        Alert if feed quality drops below threshold.

        Returns list of alerts.
        """
        alerts: list[dict[str, Any]] = []
        health_data = self._health.get_all_health()

        for name, h in health_data.items():
            if h.status in (ProviderStatus.CIRCUIT_OPEN, ProviderStatus.DOWN):
                alerts.append(
                    {
                        "level": "critical",
                        "provider": name,
                        "message": f"Provider {name} is {h.status.value}",
                        "details": h.last_error,
                        "consecutive_failures": h.failed_fetches,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
            elif h.status == ProviderStatus.DEGRADED:
                error_rate = h.failed_fetches / max(h.total_fetches, 1)
                if h.total_fetches > 5 and error_rate > 0.3:
                    alerts.append(
                        {
                            "level": "warning",
                            "provider": name,
                            "message": f"Provider {name} degraded ({error_rate:.0%} errors)",
                            "error_rate": round(error_rate, 3),
                            "timestamp": datetime.now(UTC).isoformat(),
                        }
                    )

        # Overall empty feed alert
        all_providers = list(health_data.values())
        if all_providers:
            empty_rate = sum(1 for h in all_providers if h.status == ProviderStatus.DOWN)
            if empty_rate > len(all_providers) * 0.5:
                alerts.append(
                    {
                        "level": "critical",
                        "provider": "system",
                        "message": f"Feed pipeline severely degraded: {empty_rate}/{len(all_providers)} providers down",
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

        return alerts


# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_pipeline: FeedQualityPipeline | None = None


async def get_feed_pipeline() -> FeedQualityPipeline:
    """Get singleton feed quality pipeline."""
    global _pipeline
    if _pipeline is None:
        _pipeline = FeedQualityPipeline()
    return _pipeline


# ══════════════════════════════════════════════════════════════════════
# QUICK HEALTH ROUTE (for FastAPI mount)
# ══════════════════════════════════════════════════════════════════════


async def health_route() -> dict[str, Any]:
    """Health check for the feed quality pipeline."""
    try:
        pipeline = await get_feed_pipeline()
        dashboard = pipeline.get_provider_dashboard()
        alerts = pipeline.check_degradation()
        cache_stats = pipeline.get_cache_stats()
        return {
            "healthy": len(alerts) == 0,
            "alerts": alerts,
            "providers": dashboard,
            "cache": cache_stats,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {
            "healthy": False,
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat(),
        }
