"""Body-quality scoring for news rows — the ONLY scorer (write time + backfill).

A news row carries two separate facts:

``body_source``
    * ``publisher``    — body came from the publisher page (real article text)
    * ``truncated``    — body is an RSS summary / partial fragment, not the article
    * ``google_shell`` — URL is a news.google.com interstitial, body is a fragment

``body_quality_score`` (0-100) / ``body_quality_verdict`` (``ok`` | ``thin`` | ``stub``)
    Derived from the body, never from the URL alone.

The ratings pipeline owns ``quality_score`` / ``quality_verdict``
(``ACCEPT`` | ``REJECT`` | ``NEEDS_SOURCE``). This module NEVER writes those.

Downstream RAG must refuse ``stub`` rows: a Google shell is not journalism.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

PUBLISHER = "publisher"
TRUNCATED = "truncated"
GOOGLE_SHELL = "google_shell"
BODY_SOURCES = (PUBLISHER, TRUNCATED, GOOGLE_SHELL)
VERDICTS = ("ok", "thin", "stub")

#: Any body shorter than this cannot be a complete article.
MIN_ARTICLE_CHARS = 400
#: Below this a fragment is not even a usable summary.
MIN_SUMMARY_CHARS = 120
GOOGLE_NEWS_HOSTS = frozenset({"news.google.com", "news.google.co.uk", "news.googleusercontent.com"})


@dataclass(frozen=True, slots=True)
class BodyQuality:
    """Outcome of scoring one row."""

    body_source: str
    score: int
    verdict: str

    @property
    def truncated(self) -> bool:
        """True when the stored body is not the full publisher article."""
        return self.body_source != PUBLISHER


def is_google_news_url(url: str | None) -> bool:
    """True when the URL is a Google News interstitial rather than a publisher page."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in GOOGLE_NEWS_HOSTS


def _length_points(size: int) -> int:
    """Length curve: 2,000 chars saturates (real articles are 1.5k-8k)."""
    return min(60, (size * 60) // 2000)


def _structure_points(text: str) -> int:
    """Paragraph count + sentence density — a shell has neither."""
    paragraphs = sum(1 for block in text.split("\n\n") if len(block.strip()) >= 80)
    sentences = text.count(". ") + text.count(".\n") + text.count("! ") + text.count("? ")
    return min(20, paragraphs * 5) + min(10, sentences)


def _verdict(size: int, score: int, body_source: str) -> str:
    if body_source == GOOGLE_SHELL or size < MIN_SUMMARY_CHARS:
        return "stub"
    if size < MIN_ARTICLE_CHARS or score < 45:
        return "thin"
    return "ok"


def score_body(body: str | None, url: str | None, title: str | None = None) -> BodyQuality:
    """Classify one row. Pure: no I/O, no clock, no randomness."""
    text = (body or "").strip()
    size = len(text)
    if is_google_news_url(url):
        return BodyQuality(GOOGLE_SHELL, min(20, _length_points(size)), "stub")
    body_source = PUBLISHER if size >= MIN_ARTICLE_CHARS else TRUNCATED
    score = _length_points(size) + _structure_points(text)
    score += 10 if len((title or "").strip()) >= 8 else 0
    score = max(0, min(100, score))
    return BodyQuality(body_source, score, _verdict(size, score, body_source))
