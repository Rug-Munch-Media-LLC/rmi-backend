"""Our own token score — GMGN's rank order is never reused.

Six components normalize to 0..1 and combine through fixed, versioned
weights, so any score change is auditable (``weights_version``). A component
whose inputs are missing contributes 0 *and* is reported in ``missing``, so
"thin data" is distinguishable from "genuinely weak token".
"""

from __future__ import annotations

import math
from typing import Any

from app.domains.gmgn_intel.coerce import num, whole

WEIGHTS = {
    "volume": 0.22,
    "liquidity": 0.20,
    "security": 0.20,
    "holders": 0.18,
    "social": 0.12,
    "momentum": 0.08,
}
WEIGHTS_VERSION = "2026-10-24.1"

# (field, floor, ceiling) — log-scaled, at floor => 0.0, at ceiling => 1.0.
_LOG_INPUTS = {
    "volume": ("volume_24h_usd", 1_000.0, 1_000_000.0),
    "liquidity": ("liquidity_usd", 1_000.0, 250_000.0),
    "holders": ("holders", 10.0, 2_000.0),
}


def _log_scale(value: float | None, floor: float, ceiling: float) -> float | None:
    """Log-normalize ``value`` into 0..1 between floor and ceiling."""
    if value is None or value <= 0:
        return None if value is None else 0.0
    if value <= floor:
        return 0.0
    if value >= ceiling:
        return 1.0
    return round(math.log10(value / floor) / math.log10(ceiling / floor), 4)


def _has_security(sec: dict[str, Any]) -> bool:
    """True when the security block carries at least one usable risk fact."""
    if sec.get("is_honeypot") is True or sec.get("can_not_sell") is True:
        return True
    return any(num(sec.get(field)) is not None for field in ("rug_ratio", "top_10_holder_rate", "sniper_count"))


def _security_value(sec: dict[str, Any]) -> tuple[float, list[str], bool]:
    """1.0 minus explainable penalties; a honeypot blocks the token outright."""
    flags: list[str] = []
    blocked = bool(sec.get("is_honeypot") or sec.get("can_not_sell"))
    if blocked:
        return 0.0, ["honeypot_or_unsellable"], True
    value = 1.0
    checks = (
        ("rug_ratio", 0.5, 0.35, "high_rug_ratio"),
        ("top_10_holder_rate", 0.5, 0.25, "top10_concentration"),
        ("suspected_insider_hold_rate", 0.2, 0.15, "insider_hold"),
        ("bot_degen_rate", 0.5, 0.15, "bot_concentration"),
        ("fresh_wallet_rate", 0.6, 0.10, "fresh_wallet_share"),
    )
    for field, limit, penalty, flag in checks:
        observed = num(sec.get(field))
        if observed is not None and observed > limit:
            value -= penalty
            flags.append(flag)
    if sec.get("is_wash_trading") is True:
        value -= 0.20
        flags.append("wash_trading")
    return max(0.0, round(value, 4)), flags, False


def _social_value(social: dict[str, Any] | None) -> float | None:
    """Share of the three social surfaces that are actually present."""
    if not social:
        return None
    present = sum(1 for link in social.values() if link)
    return round(present / 3, 4)


def _momentum_value(token: dict[str, Any]) -> float | None:
    """Buy/sell pressure centered on 0.5 => 0.0, 0.65+ => 1.0."""
    buys, sells = whole(token.get("buys_24h")), whole(token.get("sells_24h"))
    if buys is None or sells is None:
        return None
    total = buys + sells
    if total <= 0:
        return None
    share = buys / total
    return round(min(1.0, max(0.0, (share - 0.35) / 0.3)), 4)


def score_token(token: dict[str, Any], security: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score one normalized token with our weights; explain every point."""
    sec = security if security is not None else (token.get("security") or {})
    components: list[dict[str, Any]] = []
    missing: list[str] = []
    security_value, flags, blocked = _security_value(sec)

    for name in WEIGHTS:
        if name == "security":
            # No security facts at all must not read as "clean" — report the gap.
            value: float | None = security_value if _has_security(sec) else None
        elif name == "social":
            value = _social_value(token.get("social"))
        elif name == "momentum":
            value = _momentum_value(token)
        else:
            field, floor, ceiling = _LOG_INPUTS[name]
            value = _log_scale(num(token.get(field)), floor, ceiling)
        if value is None:
            missing.append(name)
            value = 0.0
        components.append(
            {
                "name": name,
                "value": value,
                "weight": WEIGHTS[name],
                "points": round(value * WEIGHTS[name] * 100, 2),
            }
        )

    total = 0.0 if blocked else round(sum(c["points"] for c in components), 2)
    return {
        "score": total,
        "grade": _grade(total),
        "blocked": blocked,
        "penalties": flags,
        "weights_version": WEIGHTS_VERSION,
        "components": components,
        "missing": missing,
    }


def _grade(score: float) -> str:
    """Letter grade buckets used by the feed/API consumers."""
    for threshold, letter in ((80, "A"), (65, "B"), (50, "C"), (35, "D")):
        if score >= threshold:
            return letter
    return "F"
