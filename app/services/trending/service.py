"""Route-shape adapter over the canonical engine payload.

The legacy ClickHouse/``score_candidates`` ranker that lived here is gone; the
canonical engine (``rugbrain.trending_fusion/v1``) is the only scorer. This
module keeps the ``market_intel`` trending route's response keys stable
(``data`` / ``boosted`` / ``count`` / ``updated_at`` / ``score_formula``) while
returning engine rows untouched: no arithmetic, no re-ranking, no formula.
"""

from __future__ import annotations

import time
from typing import Any

from app.trending.engine import ENGINE_ID, fetch_engine_trending

DEFAULT_CHAIN = "solana"


async def trending_scored(chain: str = "", limit: int = 50, hours: int = 24) -> dict[str, Any]:
    """Engine payload re-keyed for the legacy route; paid rows leave the rank."""
    del hours  # the window is the engine's weight config, not ours
    payload = await fetch_engine_trending(chain or DEFAULT_CHAIN, limit)
    ranked = [r for r in (payload.get("ranked") or []) if isinstance(r, dict)]
    organic = [r for r in ranked if not r.get("paid")]
    paid = [r for r in ranked if r.get("paid")]
    paid += [r for r in (payload.get("paid_only_excluded") or []) if isinstance(r, dict)]
    return {
        "chain": payload.get("chain") or (chain or DEFAULT_CHAIN),
        "data": organic,
        "boosted": paid,
        "count": len(organic),
        "boosted_count": len(paid),
        "source": ENGINE_ID,
        "score_formula": ENGINE_ID,
        "window_hours": None,
        "reason": None,
        "updated_at": payload.get("generated_at") or int(time.time()),
    }
