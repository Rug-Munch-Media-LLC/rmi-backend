"""Serialized paid response for the Rug Probability tool.

Split out of ``rug_probability.py`` so the router stays small and the response
contract (tool/version/timestamp/…/guarantee) lives in ONE place. ``envelope``
takes a single payload object: seven positional parameters invited argument-order
mistakes on a frozen public JSON shape.

The JSON shape is byte-identical to the pre-split builder — same keys, same
order, same rounding, same deprecation warning emitted once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("x402.rug_probability")

MODEL_VERSION = "rug-probability-2026-10"
DEPRECATION_RELEASE = "0.4.0"
_deprecation_logged = False

# (min probability, tier, recommendation) — highest band first.
_TIERS: tuple[tuple[int, str, str], ...] = (
    (75, "EXTREME_RISK", "DO NOT BUY - extremely high rug probability"),
    (50, "HIGH_RISK", "Avoid - significant rug indicators present"),
    (25, "MODERATE_RISK", "Caution - monitor closely before entry"),
    (10, "LOW_RISK", "Standard risk - normal market activity"),
)
_MINIMAL_RISK = ("MINIMAL_RISK", "Low rug probability - relatively safe")


@dataclass(frozen=True)
class EnvelopePayload:
    """Everything the serialized envelope needs, in one object."""

    address: str
    chain: str
    probability: int
    tier: str
    recommendation: str
    signals: list[dict[str, Any]]
    started: float


def _log_deprecation() -> None:
    """Warn once that `signals[].weight` is gone and `signals` follows in 0.4.0."""
    global _deprecation_logged
    if not _deprecation_logged:
        logger.warning(
            "rug_probability: per-signal weights removed; `signals` is deprecated and disappears in %s (use `reasons`)",
            DEPRECATION_RELEASE,
        )
        _deprecation_logged = True


def tier_for(probability: int) -> tuple[str, str]:
    """Map a 0-100 score to (tier, recommendation) — bands, never a rubric."""
    for floor, tier, recommendation in _TIERS:
        if probability >= floor:
            return tier, recommendation
    return _MINIMAL_RISK


def envelope(payload: EnvelopePayload) -> dict[str, Any]:
    """Serialized paid response: score + band + reason strings, no rubric."""
    _log_deprecation()
    return {
        "tool": "Rug Probability Score",
        "version": "2.0",
        "timestamp": datetime.now(UTC).isoformat(),
        "address": payload.address,
        "chain": payload.chain,
        "rug_probability": payload.probability,
        "tier": payload.tier,
        "recommendation": payload.recommendation,
        "reasons": [item["detail"] for item in payload.signals],
        # DEPRECATED: kept for one release; entries carry a coarse `band` and no
        # numeric weight. Removed in DEPRECATION_RELEASE.
        "signals": payload.signals,
        "signal_count": len(payload.signals),
        "method": "banded_signal_sum",
        "model_version": MODEL_VERSION,
        "performance_ms": round((time.time() - payload.started) * 1000, 1),
        "guarantee": "Data delivered or auto-refund via x402 receipt",
    }
