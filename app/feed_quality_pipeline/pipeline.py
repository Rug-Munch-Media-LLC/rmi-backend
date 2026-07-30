"""
Feed Quality Pipeline orchestrator — fetches, validates, deduplicates, scores.

Exports:
    FeedQualityPipeline, get_feed_pipeline, health_route
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.feed_quality_pipeline.cache import (
    LRUCache,
    MAX_CONCURRENT_FETCHES,
    MAX_POSTS_PER_PROVIDER,
    MAX_POST_LENGTH,
    MAX_TITLE_LENGTH,
    PROVIDER_TIMEOUT_DEFAULT,
    SEEN_IDS_MAXSIZE,
)
from app.feed_quality_pipeline.deduplicator import ContentDeduplicator
from app.feed_quality_pipeline.health import ProviderHealthMonitor
from app.feed_quality_pipeline.models import FeedPost, ProviderStatus, QualityMetrics
from app.feed_quality_pipeline.scorer import FeedScorer
from app.feed_quality_pipeline.validator import ContentValidator

logger = get_logger(__name__)


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
        - Slow provider: timeout raises asyncio.TimeoutError -> tracked as failure
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
                error="Circuit breaker open, skipping until reset",
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

        except TimeoutError:
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
            user_preferences: optional category -> weight mapping
            creator_affinities: optional author -> affinity mapping

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


__all__ = [
    "FeedQualityPipeline",
    "get_feed_pipeline",
    "health_route",
]
