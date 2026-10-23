"""Earnings estimate derived strictly from a partner's configured rate card."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domains.poly_attribution.rate_card import load_rate_card


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return Decimal(str(value))


def estimate_earnings(partner: str, counts: dict[str, int], gross_usd: Decimal | None) -> dict[str, Any]:
    """Estimate earnings, or return ``earnings_usd=None`` with a reason.

    Rules (all must come from ``POLY_ATTRIBUTION_RATES``):
      * ``revshare_pct`` — percentage of attributed ``value_usd`` gross.
      * ``cpa`` — flat USD per attributed event type (``signup``/``order``).
    """
    card = load_rate_card(partner)
    if card is None:
        return {"earnings_usd": None, "reason": "no_rate_card_configured", "basis": None}

    total = Decimal("0")
    basis: dict[str, str] = {}

    revshare = _as_decimal(card.get("revshare_pct"))
    if revshare is not None and gross_usd is not None:
        share = revshare / Decimal("100") * gross_usd
        total += share
        basis["revshare"] = str(share.quantize(Decimal("0.000001")))

    cpa = card.get("cpa")
    if isinstance(cpa, dict):
        for event_type, rate in cpa.items():
            amount = _as_decimal(rate)
            if amount is None:
                continue
            paid = amount * counts.get(str(event_type), 0)
            if paid:
                total += paid
                basis[f"cpa:{event_type}"] = str(paid.quantize(Decimal("0.000001")))

    if not basis:
        return {"earnings_usd": None, "reason": "rate_card_incomplete", "basis": None}

    return {
        "earnings_usd": str(total.quantize(Decimal("0.000001"))),
        "reason": None,
        "basis": basis,
    }
