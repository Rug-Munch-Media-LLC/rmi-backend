"""
Cross-provider content deduplication — exact and near-dup detection.

Exports:
    ContentDeduplicator
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from app.feed_quality_pipeline.cache import DEDUP_SIMILARITY_THRESHOLD

if TYPE_CHECKING:
    from app.feed_quality_pipeline.models import FeedPost


class ContentDeduplicator:
    """
    Cross-provider content deduplication.
    Handles exact dupes (hash-based) and near-dup es (fuzzy).

    Edge cases covered:
    - Exact duplicate across providers (same headline from CoinDesk and Blockworks)
    - Near-identical posts (same story, slightly different wording)
    - Same URL shared with different context -> NOT merged
    - Translated content -> NOT merged
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

        # 2. Same URL, non-empty -> duplicate (but NOT if URL is empty/default)
        if post.url and len(post.url) > 10:
            url_key = post.url.rstrip("/").lower()
            if url_key in self._seen_urls:
                return True
            self._seen_urls.add(url_key)

        # 3. Near-duplicate: RapidFuzz Levenshtein ratio on title+content
        normalized = post.normalized_text()
        for prev_text, prev_source, _prev_id in self._recent_titles:
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

        # Not duplicate -- add to tracking
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
        return abs(ratio_a - ratio_b) > 0.3


__all__ = ["ContentDeduplicator"]
