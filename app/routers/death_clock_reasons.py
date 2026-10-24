"""Qualitative reason strings for the Death Clock prediction.

The threshold ladder lives here as a TUPLE TABLE, not as nested branches: the
evaluator is a lookup plus a message append, so the ladder can grow without
growing cyclomatic complexity. Messages carry no weights and no numeric
contribution — only the direction word a caller is allowed to see.

Kept out of ``death_clock.py`` so that module stays a thin router.
"""

from __future__ import annotations

from typing import Any

FALLBACK_REASON = "no adverse signal found in the available features"
MAX_REASONS = 4

# (group, field, op, argument, message) — first match per group wins.
#   truthy / falsy        -> value is non-zero / zero
#   lt / gt               -> value < arg / value > arg
#   gt_lt                 -> arg[0] < value < arg[1]
#   ge_lt                 -> arg[0] <= value < arg[1]
_REASON_LADDER: tuple[tuple[str, str, str, Any, str], ...] = (
    (
        "honeypot",
        "honeypot_risk",
        "truthy",
        0,
        "honeypot behaviour reported: holders may be unable to sell",
    ),
    ("liquidity", "liquidity_usd", "lt", 1000, "liquidity is critically thin"),
    ("liquidity", "liquidity_usd", "lt", 10000, "liquidity is thin for the current holder base"),
    ("liquidity", "liquidity_usd", "lt", 50000, "liquidity is modest"),
    ("lp_lock", "lp_locked_pct", "gt_lt", (0, 50), "a large share of the LP is unlocked"),
    ("renounce", "owner_renounced", "falsy", 0, "owner has not renounced control"),
    ("mint", "mint_authority", "truthy", 0, "mint authority is still active"),
    ("tax", "max_tax", "gt", 0.10, "trading taxes are high"),
    ("age", "age_days", "ge_lt", (0, 1), "token is less than a day old"),
    (
        "churn",
        "volume_to_liquidity",
        "gt",
        3,
        "volume is large relative to liquidity (high churn)",
    ),
)


def as_number(value: Any) -> float:
    """Coerce a scanner feature to a float (booleans and y/yes become 1.0)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        return 1.0 if value.lower() in ("yes", "true", "honeypot") else 0.0
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _feature(features: dict[str, Any], field: str) -> float:
    if field == "max_tax":
        return max(as_number(features.get("buy_tax")), as_number(features.get("sell_tax")))
    return as_number(features.get(field))


def _matches(op: str, value: float, arg: Any) -> bool:
    if op == "truthy":
        return value != 0
    if op == "falsy":
        return value == 0
    if op == "lt":
        return value < arg
    if op == "gt":
        return value > arg
    if op == "gt_lt":
        return arg[0] < value < arg[1]
    return arg[0] <= value < arg[1]  # ge_lt


def reasons_for(features: dict[str, Any]) -> list[str]:
    """Banded reasons for a feature map — no thresholds or weights leave here."""
    reasons: list[str] = []
    matched: set[str] = set()
    for group, field, op, arg, message in _REASON_LADDER:
        if group in matched:
            continue
        if _matches(op, _feature(features, field), arg):
            reasons.append(message)
            matched.add(group)
    return reasons[:MAX_REASONS] or [FALLBACK_REASON]
