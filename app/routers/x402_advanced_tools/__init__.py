"""
Advanced x402 tools - caching, confidence, real-time streams, predictive scoring.

Split from monolithic x402_advanced_tools.py into:
  - cache.py       Layer 1: Response caching
  - confidence.py  Layer 2: Confidence scores
  - stream.py      Layer 3: SSE alert stream
  - models.py      Request models
  - rug_probability.py  Layer 4: Rug probability
  - history.py     Layer 5: Historical scanner data
  - narrative.py   Layer 6: Narrative engine
"""

from __future__ import annotations
from app.routers.x402_advanced_tools.cache import get_cached, invalidate_cache, set_cached
from app.routers.x402_advanced_tools.confidence import compute_confidence
from app.routers.x402_advanced_tools.history import token_history
from app.routers.x402_advanced_tools.models import AddressRequest, HistoryRequest, NarrativeRequest
from app.routers.x402_advanced_tools.narrative import narrative_engine
from app.routers.x402_advanced_tools.rug_probability import rug_probability
from app.routers.x402_advanced_tools.stream import sse_alert_stream

from fastapi import APIRouter, Query
from datetime import UTC, datetime

router = APIRouter()

# Register routes
router.add_api_route("/stream/alerts", sse_alert_stream, methods=["GET"])
router.add_api_route("/rug_probability", rug_probability, methods=["POST"])
router.add_api_route("/history", token_history, methods=["POST"])
router.add_api_route("/narrative", narrative_engine, methods=["POST"])

@router.post("/cache/clear")
async def clear_cache(tool: str = Query(default=None)) -> dict[str, str]:
    """Clear response cache for a specific tool or all tools."""
    ok = invalidate_cache(tool)
    return {
        "status": "cleared" if ok else "failed",
        "tool": tool or "all",
        "timestamp": datetime.now(UTC).isoformat(),
    }

@router.get("/cache/stats")
async def cache_stats() -> dict[str, int] | dict[str, str]:
    """Get cache hit/miss statistics."""
    try:
        from app.routers.x402_advanced_tools.cache import _redis_conn
        import json

        r = _redis_conn()
        keys = list(r.scan_iter("x402:cache:*"))
        return {
            "cached_entries": len(keys),
            "memory_estimate_bytes": sum(len(r.get(k) or "") for k in keys[:100]),
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        return {"error": str(e), "cached_entries": 0}

__all__ = [
    "router",
    "get_cached",
    "set_cached",
    "invalidate_cache",
    "compute_confidence",
    "AddressRequest",
    "HistoryRequest",
    "NarrativeRequest",
    "rug_probability",
    "token_history",
    "narrative_engine",
    "sse_alert_stream",
]
