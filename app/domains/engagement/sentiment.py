"""Sentiment rollup derivation — deterministic, and honest when there is no signal.

There is no default classifier. A score is published only from observed signal:
reactions, or a classifier score handed in by an explicit hook. With neither, the
rollup carries ``score=None`` plus a machine-readable reason, never a 0.0 guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domains.engagement.kinds import POLARITY, REACTION_KINDS

# Reactions needed before a reaction-only score is published. Below this the
# bucket counts are exposed but the score stays NULL.
MIN_SIGNAL: int = 3

_REASON_NO_REACTIONS = "no_reactions"
_REASON_INSUFFICIENT = "insufficient_reactions"
_REASON_CLASSIFIER = "classifier_only"
_REASON_REACTIONS = "reactions"
_REASON_BLENDED = "reactions_plus_classifier"


@dataclass(frozen=True, slots=True)
class SentimentRollup:
    """Bucketed counts plus an optional -100..+100 score."""

    positive: int
    neutral: int
    negative: int
    sample_size: int
    score: float | None
    reason: str

    @property
    def has_score(self) -> bool:
        return self.score is not None


def _clamp(value: float) -> float:
    """Clamp an external classifier score into the documented -100..+100 range."""
    return max(-100.0, min(100.0, value))


def bucket_counts(counts: dict[str, int]) -> tuple[int, int, int]:
    """(positive, neutral, negative) from raw per-kind counts."""
    positive = sum(counts.get(kind, 0) for kind in REACTION_KINDS if POLARITY[kind] > 0)
    neutral = sum(counts.get(kind, 0) for kind in REACTION_KINDS if POLARITY[kind] == 0)
    negative = sum(counts.get(kind, 0) for kind in REACTION_KINDS if POLARITY[kind] < 0)
    return positive, neutral, negative


def derive(
    counts: dict[str, int],
    *,
    classifier_score: float | None = None,
    min_signal: int = MIN_SIGNAL,
) -> SentimentRollup:
    """Build the rollup. ``score`` is None whenever the evidence is too thin."""
    positive, neutral, negative = bucket_counts(counts)
    sample = positive + neutral + negative

    if classifier_score is not None:
        classifier = _clamp(float(classifier_score))
        if sample == 0:
            return SentimentRollup(0, 0, 0, 0, round(classifier, 2), _REASON_CLASSIFIER)
        reaction_score = (positive - negative) / sample * 100.0
        blended = round(reaction_score * 0.5 + classifier * 0.5, 2)
        return SentimentRollup(positive, neutral, negative, sample, blended, _REASON_BLENDED)

    if sample == 0:
        return SentimentRollup(0, 0, 0, 0, None, _REASON_NO_REACTIONS)
    if sample < min_signal:
        return SentimentRollup(positive, neutral, negative, sample, None, _REASON_INSUFFICIENT)

    score = round((positive - negative) / sample * 100.0, 2)
    return SentimentRollup(positive, neutral, negative, sample, score, _REASON_REACTIONS)
