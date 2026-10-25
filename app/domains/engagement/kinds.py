"""Canonical reaction vocabulary and its sentiment polarity.

One source of truth: the news engagement router accepts the same six kinds, so a
client never has two vocabularies and the rollup never silently drops a kind.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

ReactionKind = Literal["like", "dislike", "bullish", "bearish", "important", "scam"]

REACTION_KINDS: Final[tuple[str, ...]] = get_args(ReactionKind)

# Sentiment polarity per kind: 1 = positive, 0 = neutral, -1 = negative.
# ``important`` is deliberately neutral — it is a relevance signal, not a vote
# on the article's direction, and treating it as positive would bias coverage.
POLARITY: Final[dict[str, int]] = {
    "like": 1,
    "bullish": 1,
    "important": 0,
    "dislike": -1,
    "bearish": -1,
    "scam": -1,
}

BUCKETS: Final[tuple[str, str, str]] = ("positive", "neutral", "negative")


def coerce_kind(value: str | None) -> str | None:
    """Return ``value`` when it is a known kind, else ``None``."""
    return value if value in POLARITY else None


def bucket_for(kind: str) -> str:
    """Sentiment bucket for a reaction kind; unknown kinds count as neutral."""
    polarity = POLARITY.get(kind)
    return "neutral" if polarity is None else BUCKETS[1 - polarity]
