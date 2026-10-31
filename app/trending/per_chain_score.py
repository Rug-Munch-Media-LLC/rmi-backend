"""Per-chain trending score: components, weights, guards — all visible.

Weights sum to 100 across eight inputs. Two of them are not yet indexable
from our own tables (``liquidity``: no pool reserves in ``dex_events``;
``holders``: ``holder_balances`` is a stale snapshot), so the denominator is
built from the weights that are actually present. A component that is missing
shrinks the denominator instead of scoring zero — a missing input must not
punish a token.

Guards are multiplicative and always reported, so a demotion is explainable.
Wash detection does not use ``sender = recipient`` (the indexer writes the
trader into both fields, so that comparison is 100% true and meaningless).
"""

from __future__ import annotations

import math
from typing import Any

WEIGHTS: dict[str, float] = {
    "volume": 28.0,
    "velocity": 14.0,
    "breadth": 14.0,
    "pressure": 8.0,
    "momentum": 8.0,
    "freshness": 8.0,
    "holders": 10.0,
    "liquidity": 10.0,
}
UNAVAILABLE = {"liquidity": "pool_reserves_not_indexed"}
RERANK_LIMIT = 400


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _momentum(first_px: float | None, last_px: float | None) -> float | None:
    """Price change mapped from [-50%, +150%] onto 0..1; None when undefined."""
    if not first_px or first_px <= 0 or last_px is None:
        return None
    return (
        _clip((float(last_px) - float(first_px)) / float(first_px) * 100.0 + 50.0, 0.0, 200.0)
        / 200.0
    )


def _components(row: dict[str, Any]) -> dict[str, float | None]:
    vol = float(row.get("vol") or 0.0)
    swaps = int(row.get("swaps") or 0)
    buyers = int(row.get("buyers") or 0)
    window_s = max(int(row.get("window_s") or 1), 1)
    age_s = row.get("age_s")
    top10 = row.get("top10_holder_share")
    return {
        "volume": _clip(math.log10(1.0 + vol) / 6.0),
        "velocity": _clip(swaps / 300.0),
        "breadth": _clip(buyers / 100.0),
        "pressure": _clip(float(row.get("buy_share") or 0.0)),
        "momentum": _momentum(row.get("first_px"), row.get("last_px")),
        "freshness": None if age_s is None else _clip(1.0 - float(age_s) / window_s),
        "holders": None if top10 is None else _clip(1.0 - float(top10)),
        "liquidity": None,
    }


def _guards(row: dict[str, Any], risk_multiplier: float, scam_hit: bool) -> dict[str, float]:
    """Anti-manipulation multipliers, named so a drop is never unexplained."""
    swaps = int(row.get("swaps") or 0)
    traders = max(int(row.get("traders") or 0), 1)
    window_s = max(int(row.get("window_s") or 1), 1)
    stale_s = int(row.get("stale_s") or 0)
    guards = {
        "single_wallet_pump": 0.5 if float(row.get("top1_share") or 0.0) > 0.5 else 1.0,
        "churn": 0.6 if swaps / traders > 15 else 1.0,
        "round_trip": 0.7 if float(row.get("roundtrip_share") or 0.0) > 0.5 else 1.0,
        "stale_window": 0.7 if stale_s > 0.25 * window_s else 1.0,
        "risk": risk_multiplier,
        "scam_overlap": 0.2 if scam_hit else 1.0,
    }
    return guards


def score_components(
    row: dict[str, Any],
    *,
    risk_multiplier: float = 1.0,
    scam_hit: bool = False,
) -> dict[str, Any]:
    """Return ``{score, components, guards, coverage}`` for one candidate row."""
    comps = _components(row)
    present = {k: v for k, v in comps.items() if v is not None}
    weighted = sum(WEIGHTS[k] * v for k, v in present.items())
    available_weight = sum(WEIGHTS[k] for k in present)
    raw = 100.0 * weighted / available_weight if available_weight else 0.0
    guards = _guards(row, risk_multiplier, scam_hit)
    multiplier = 1.0
    for factor in guards.values():
        multiplier *= factor
    return {
        "score": round(_clip(raw * multiplier, 0.0, 100.0), 2),
        "components": {k: (None if v is None else round(v, 4)) for k, v in comps.items()},
        "guards": {k: round(v, 3) for k, v in guards.items()},
        "coverage": round(available_weight / 100.0, 3),
        "unavailable": dict(UNAVAILABLE),
    }
