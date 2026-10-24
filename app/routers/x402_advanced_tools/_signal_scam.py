"""Deployer-history signal for ``rug_probability``.

Extracted verbatim from the router. A scam-pattern match contributes
``min(risk_score, 30)`` points; a `high` reason is appended only when the
detector also returned named patterns, so the score can move without a reason.
"""

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger("x402.rug_probability")


def scam_hits(addr: str, chain: str) -> list[tuple[int, dict[str, Any] | None]]:
    """Return ``(points, signal)`` from the deployer-history detector."""
    try:
        from app.integrations.rag_helpers.scam_patterns import detect_scam_patterns

        result = asyncio.run(detect_scam_patterns({"address": addr, "chain": chain}, 0.4))
    except (ImportError, RuntimeError, Exception) as e:
        logger.debug(f"Scam detection failed: {e}")
        return []

    if not result or result.get("risk_score", 0) <= 0:
        return []

    risk = result.get("risk_score", 0)
    patterns = result.get("patterns", [])
    signal = None
    if patterns:
        signal = {
            "signal": "scam_pattern_match",
            "band": "high",
            "detail": f"Matches: {', '.join(patterns[:3])}",
        }
    return [(min(risk, 30), signal)]


__all__ = ["scam_hits"]
