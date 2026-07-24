from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger("news_feed")
router = APIRouter(prefix="/api/v1/internal", tags=["internal"])
_posted: set[str] = set()

@router.get("/news-feed")
async def get_news_feed(
    channel: str = Query("news"), limit: int = Query(3), min_score: float = Query(44.0)
) -> dict[str, Any]:
    try:
        from app.caching_shield.news_network import get_feed

        feed = get_feed(sort="impact", limit=50)
        unposted = []
        for a in feed.get("articles", []):
            if a["url"] in _posted or a["id"] in _posted:
                continue
            tier = a.get("source_tier", 5)
            quality = max(0, 100 - (tier * 8) + (abs(a.get("sentiment_score", 0)) * 15))
            if quality < min_score:
                continue
            a["quality_score"] = round(quality, 1)
            unposted.append(a)
        selected = unposted[:limit]
        for a in selected:
            _posted.add(a["url"])
            _posted.add(a["id"])
        if len(_posted) > 10000:
            _posted.clear()
        return {
            "items": selected,
            "total_unposted": len(unposted),
            "ts": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"news_feed error: {e}")
        return {"items": [], "total_unposted": 0, "error": str(e)}

@router.post("/news-fetch")
async def trigger_fetch() -> dict[str, Any]:
    try:
        from app.caching_shield.news_network import fetch_all

        articles = await fetch_all()
        return {"status": "ok", "articles_fetched": len(articles)}
    except Exception as e:
        return {"status": "error", "error": str(e)}
