"""
Feed scoring — quality, recency, engagement, and personalization.

Exports:
    FeedScorer
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.feed_quality_pipeline.models import FeedPost


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

    # Source tier -> credibility mapping
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

    # Provider group -> default credibility
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

        Edge case: missing publish date -> assume moderately fresh (0.5)
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
        score = max(0.01, min(1.0, 1.0 * (2.0 ** (-age_hours / cls.RECENCY_HALF_LIFE_HOURS))))
        return score

    @classmethod
    def _score_engagement(cls, post: FeedPost) -> float:
        """
        Score based on engagement signals.
        Zero engagement -> returns baseline (not zero, not negative).
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

        preferences: dict of category -> weight (0.0 to 1.0)
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


__all__ = ["FeedScorer"]
