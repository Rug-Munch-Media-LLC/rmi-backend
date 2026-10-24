"""Boost plan catalog — the single source of pricing truth.

Prices are quoted in an explicit currency (``USD``, whole cents padded to 6
decimals) and are *duration only*. Placement is a paid, clearly-labelled slot
in the ``boosted`` surface; it never buys trending rank (see
``app/domains/boost/listing.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BoostPlan:
    """An immutable, published price point."""

    plan_id: str
    duration_hours: int
    price_amount: Decimal
    currency: str
    label: str


CURRENCY = "USD"

PLANS: dict[str, BoostPlan] = {
    "24h": BoostPlan(
        plan_id="24h",
        duration_hours=24,
        price_amount=Decimal("49.00"),
        currency=CURRENCY,
        label="24 hours",
    ),
    "72h": BoostPlan(
        plan_id="72h",
        duration_hours=72,
        price_amount=Decimal("119.00"),
        currency=CURRENCY,
        label="72 hours",
    ),
    "7d": BoostPlan(
        plan_id="7d",
        duration_hours=168,
        price_amount=Decimal("299.00"),
        currency=CURRENCY,
        label="7 days",
    ),
}


class InvalidPlanError(ValueError):
    """Raised when a plan id is not in the published catalog."""

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id
        super().__init__(f"unknown boost plan {plan_id!r}; known: {sorted(PLANS)}")


def get_plan(plan_id: str) -> BoostPlan:
    """Resolve a plan id to its immutable price point.

    Raises ``InvalidPlanError`` — callers map it to HTTP 422.
    """
    plan = PLANS.get(plan_id)
    if plan is None:
        raise InvalidPlanError(plan_id)
    return plan
