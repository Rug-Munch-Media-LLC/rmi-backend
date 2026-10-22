"""Multi-signal confidence for the bundle graph.

The competitors' failure mode is naming one heuristic a "bundle". Here the
verdict is gated by **independent signal families**:

* ``timing``   -- same-slot / first-slots co-buying,
* ``funding``  -- a shared funder (value transfer or creator payment),
* ``size``     -- near-identical sizing or a dominant share of early flow,
* ``launch``   -- exchange withdrawal or bridge-in inside the launch window.

One family alone is reported as ``weak`` and never as a bundle. Two families
reach ``strong``; three or more reach ``confirmed``. The number returned is a
*summary of what fired*, not a probability, and the tier is what consumers
should branch on.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Which independent family each signal belongs to.
FAMILIES: dict[str, str] = {
    "same_slot_buys": "timing",
    "early_slot_cluster": "timing",
    "shared_funder": "funding",
    "creator_funded": "funding",
    "similar_size": "size",
    "cex_within_window": "launch",
    "bridge_in": "launch",
}

#: Base contribution per signal: timing is cheap to produce, funding is not.
WEIGHTS: dict[str, int] = {
    "same_slot_buys": 22,
    "early_slot_cluster": 18,
    "shared_funder": 30,
    "creator_funded": 20,
    "similar_size": 14,
    "cex_within_window": 18,
    "bridge_in": 16,
}

TIER_ORDER = ("none", "weak", "strong", "confirmed")


def families_for(signals: Iterable[str]) -> set[str]:
    """Independent families represented by the fired signals."""
    return {FAMILIES[s] for s in signals if s in FAMILIES}


def score_confidence(signals: Iterable[str], extra: int = 0) -> tuple[int, str]:
    """Return ``(score_0_100, tier)`` for the fired signals.

    Args:
        signals: Signal names that actually fired.
        extra: Bonus from measured size (cluster share of supply / volume),
            capped at 15 so size can strengthen a verdict but never create one.
    """
    fired = [s for s in set(signals) if s in WEIGHTS]
    if not fired:
        return 0, "none"

    families = families_for(fired)
    raw = sum(WEIGHTS[s] for s in fired) + max(0, min(15, extra))

    if len(families) <= 1:
        # Single family: cap hard. A timing-only cluster is a sniper cluster.
        return min(45, raw), "weak"
    if len(families) == 2:
        return min(85, raw), "strong"
    return min(97, raw), "confirmed"


def is_bundle(tier: str) -> bool:
    """True only for tiers that multi-signal evidence supports."""
    return tier in ("strong", "confirmed")


def tier_rank(tier: str) -> int:
    """Ordinal rank of a tier (``-1`` for unknown values)."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1
