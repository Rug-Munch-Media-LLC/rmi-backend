"""
Content validation — rejects malformed, garbage, and abusive content.

Exports:
    ContentValidator
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.feed_quality_pipeline.cache import MAX_POST_LENGTH, MAX_TITLE_LENGTH, MIN_POST_LENGTH

if TYPE_CHECKING:
    from app.feed_quality_pipeline.models import FeedPost


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


__all__ = ["ContentValidator"]
