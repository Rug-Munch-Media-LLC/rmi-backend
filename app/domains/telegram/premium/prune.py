"""Deterministic truncation: the order in which a card gives up detail.

Telegram rejects anything over 4,096 characters with HTTP 400 and the whole
alert is lost. Rather than slice the string (which cuts mid-tag and destroys
the header), the card is *re-rendered* from a reduced :class:`CardData` after
each step below. The steps run in this order and are never re-ordered:

1. KOL block            5. cohort realised column
2. dev-history block    6. team cohort row
3. flags down to two    7. dev bag row
4. price-impact rows    8. authority addresses, then flags down to one

Never pruned, in any tier: the header, the verdict, the three core numbers,
and the link. A card that cannot fit without them is a bug, not a shorter card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domains.telegram.premium.model import CardData, Cohort

if TYPE_CHECKING:
    from collections.abc import Callable

KEEP_MIN_FLAGS = 1


@dataclass(frozen=True)
class Prune:
    """One step: drop a block, or re-render from a reduced payload."""

    name: str
    drop: str | None = None
    apply: Callable[[CardData], CardData] | None = None


def _keep_flags(count: int) -> Callable[[CardData], CardData]:
    def apply(d: CardData) -> CardData:
        kept = d.flags[:count]
        return d.with_flags(kept, d.flags_dropped + len(d.flags) - len(kept))

    return apply


def _without_realised(d: CardData) -> CardData:
    return d.replace(
        cohorts=tuple(Cohort(c.name, c.wallets, c.initial_sol, c.sold_pct, None, c.estimated) for c in d.cohorts)
    )


def _without_team(d: CardData) -> CardData:
    return d.replace(cohorts=tuple(c for c in d.cohorts if c.name != "Team"))


PRO_PLAN = (
    Prune("kol"),
    Prune("dev"),
    Prune("flags_two", apply=_keep_flags(2)),
    Prune("impact_rows", apply=lambda d: d.replace(impact_depth={})),
    Prune("cohort_realised", apply=_without_realised),
    Prune("cohort_team", apply=_without_team),
    Prune("dev_bag", apply=lambda d: d.replace(dev_holdings_pct=None)),
    Prune(
        "authority_addr",
        apply=lambda d: d.replace(
            mint_authority=_compact(d.mint_authority), freeze_authority=_compact(d.freeze_authority)
        ),
    ),
    Prune("flags_one", apply=_keep_flags(KEEP_MIN_FLAGS)),
    Prune("legend"),
)

FREE_PLAN = (Prune("legend"),)
ALERT_PLAN = ()


def _compact(authority: str | None) -> str | None:
    """Keep the state, lose the address — 'active' still reads as a live switch."""
    return "active" if authority not in (None, "revoked") else authority


def prune_plan(tier_name: str) -> tuple[Prune, ...]:
    """The ordered plan for a tier; unknown tiers get the free plan."""
    return {"pro": PRO_PLAN, "alert": ALERT_PLAN}.get(tier_name, FREE_PLAN)
