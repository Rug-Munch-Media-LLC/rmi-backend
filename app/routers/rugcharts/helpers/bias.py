"""Banded predictive bias from trade-flow evidence.

Extracted from ``routes/intel.py`` so the route module stays under the size cap.
Output contract is unchanged: a band word, a bounded score and reason strings —
no numeric per-factor impacts, no weights.
"""

from __future__ import annotations

from typing import Any


def _bot_factor(breakdown: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    if breakdown["bot_volume_pct"] is None:
        return 0, None
    pct = breakdown["bot_volume_pct"]
    if pct >= 40:
        return -25, {
            "factor": "bot_dominance",
            "band": "strong_bearish",
            "evidence": f"{pct}% of sampled volume is bot-labeled",
        }
    if pct >= 15:
        return -10, {"factor": "bot_elevated", "band": "bearish", "evidence": f"{pct}% bot volume"}
    return 5, {
        "factor": "organic_flow",
        "band": "mild_bullish",
        "evidence": f"bot share only {pct}%",
    }


def _pressure_factor(breakdown: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    bp = breakdown["buy_pressure"]
    if bp is None:
        return 0, None
    if bp >= 0.65:
        return 15, {
            "factor": "buy_pressure",
            "band": "bullish",
            "evidence": f"{bp:.0%} buy-side volume",
        }
    if bp <= 0.35:
        return -15, {
            "factor": "sell_pressure",
            "band": "bearish",
            "evidence": f"only {bp:.0%} buy-side",
        }
    return 0, None


def predictive_bias(breakdown: dict[str, Any]) -> dict[str, Any]:
    """Banded bias from trade-flow evidence: reasons, never numeric impacts."""
    factors: list[dict[str, Any]] = []
    bias_score = 0
    for delta, factor in (_bot_factor(breakdown), _pressure_factor(breakdown)):
        bias_score += delta
        if factor is not None:
            factors.append(factor)
    bias = "BEARISH" if bias_score <= -20 else "BULLISH" if bias_score >= 20 else "NEUTRAL"
    return {
        "bias": bias,
        "score": max(-100, min(100, bias_score)),
        "reasons": [item["evidence"] for item in factors],
        # DEPRECATED: entries keep `factor` + `evidence` and carry a coarse
        # `band` instead of a numeric `impact`. Removed in 0.4.0.
        "factors": factors,
        "method": "banded_rule_fusion",
        "model_version": "bias-band-2026-10",
        "disclaimer": "Heuristic bias from trade-flow evidence — not financial advice.",
    }
