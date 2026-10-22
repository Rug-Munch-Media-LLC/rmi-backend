"""Tier definitions: what each card is allowed to show.

A tier is a contract, not a size. ``free`` answers "should I touch this?",
``pro`` answers "who is holding the bag and when do they leave?", ``alert``
answers "what changed since you last looked?" and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    name: str
    sections: tuple[str, ...]
    promise: str


FREE = Tier(
    name="free",
    sections=("verdict", "core", "flag1", "link"),
    promise="verdict, risk word, three numbers, one flag, one link",
)
PRO = Tier(
    name="pro",
    sections=(
        "meta",
        "verdict",
        "cohort",
        "supply",
        "market",
        "authorities",
        "dev",
        "kol",
        "flags",
        "legend",
        "link",
    ),
    promise="cohort rollup, bundle share, authorities, LP, price impact, dev, KOL",
)
ALERT = Tier(
    name="alert",
    sections=("delta", "link"),
    promise="the delta only, with a link to the full scan",
)

TIERS = {t.name: t for t in (FREE, PRO, ALERT)}


def tier(name: str) -> Tier:
    """Resolve a tier name; ``free`` is the safe default for unknown input."""
    return TIERS.get(str(name).strip().lower(), FREE)
