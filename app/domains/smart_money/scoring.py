"""Documented scoring weights for smart-money moves.

score = sum(weight_i * component_i) over the components that have evidence;
confidence = share of total weight that had real evidence. Missing evidence
never contributes a guessed mid-point - it lowers confidence instead.
"""

from __future__ import annotations

# Weights MUST sum to 1.0. Changing them is a behaviour change: bump the
# version string so downstream caches keyed on it invalidate.
SCORING_VERSION = "1.0.0"

WEIGHTS: dict[str, float] = {
    "realized_pnl": 0.25,   # net USD flow (outflow - inflow) on the window
    "win_rate": 0.20,       # share of profitable token positions
    "conviction": 0.15,     # move size vs the wallet's own typical move
    "freshness": 0.15,      # how recent within the window (exp decay)
    "cex_funding": 0.10,    # CEX-funded wallets are tracked, not copied
    "cluster": 0.10,        # wallet is part of a labelled cluster
    "liquidity": 0.05,      # traded notional relative to the window median
}

assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "weights must sum to 1.0"


def _unit(x: float | None, scale: float) -> float | None:
    """Clamp ``x / scale`` into [0, 1]; ``None`` passes through."""
    if x is None:
        return None
    return max(0.0, min(1.0, x / scale if scale else 0.0))


def score_components(
    *,
    pnl_usd: float | None,
    pnl_pct: float | None,
    win_rate: float | None,
    amount_usd: float,
    median_amount_usd: float,
    age_hours: float,
    window_hours: float,
    cex_related: bool | None,
    cluster_id: str | None,
) -> tuple[float, float, list[str]]:
    """Return ``(score, confidence, reasons)`` for one candidate move."""
    reasons: list[str] = []
    parts: dict[str, float | None] = {}

    if pnl_usd is not None and pnl_pct is not None:
        # +50% ROI saturates the component; negative flow scores 0.
        parts["realized_pnl"] = _unit(pnl_pct, 0.5)
        reasons.append(f"pnl_usd={pnl_usd:.2f} pnl_pct={pnl_pct:.4f}")
    else:
        parts["realized_pnl"] = None
        reasons.append("pnl:insufficient_matched_flow")

    parts["win_rate"] = win_rate
    if win_rate is None:
        reasons.append("win_rate:insufficient_closed_positions")

    parts["conviction"] = _unit(amount_usd, median_amount_usd * 5 or amount_usd)
    parts["freshness"] = max(0.0, 1.0 - (age_hours / window_hours)) if window_hours else 0.0
    parts["liquidity"] = _unit(amount_usd, 250_000.0)

    if cex_related is None:
        parts["cex_funding"] = None
        reasons.append("cex_funding:unknown")
    else:
        parts["cex_funding"] = 0.0 if cex_related else 1.0
        reasons.append(f"cex_related={cex_related}")

    if cluster_id:
        parts["cluster"] = 1.0
    else:
        parts["cluster"] = None
        reasons.append("cluster:unlabelled")

    total = 0.0
    seen = 0.0
    for name, weight in WEIGHTS.items():
        value = parts.get(name)
        if value is None:
            continue
        total += weight * value
        seen += weight

    score = total / seen if seen else 0.0
    return round(score, 4), round(seen, 4), reasons
