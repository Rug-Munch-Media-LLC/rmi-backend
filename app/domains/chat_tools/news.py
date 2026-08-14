"""News executor for the chat tool-use layer.

Top headlines from the Qdrant news_v2 feed, newest first. Reuses the Qdrant
news_v2 helpers in app/routers/news (imported lazily) — never re-implements a
news query. Fail-open: returns ``None`` on any error/timeout and NEVER raises.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_NEWS_SCROLL_WINDOW = 50  # scroll a window, then keep the newest 10
_NEWS_MAX_ITEMS = 10


def _relative_time(published_at: str | None) -> str:
    """Compact "3h ago" from an ISO-8601 timestamp; "" when unparseable."""
    if not published_at:
        return ""
    try:
        raw = str(published_at).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        seconds = max(int((datetime.now(UTC) - dt).total_seconds()), 0)
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except (TypeError, ValueError, OverflowError):
        return ""


async def _exec_news(message: str = "") -> str | None:
    """Top headlines from the Qdrant news_v2 feed, newest first. Fail-open.

    Calls the in-process ``_scroll_news_v2`` / ``_point_to_article`` helpers
    from ``app.routers.news`` (never the HTTP endpoints, never a second
    network round-trip beyond the single scroll). Renders a compact
    "Top stories" block capped at 10 items; returns None on failure/empty.
    """
    try:
        from app.routers.news import _point_to_article, _scroll_news_v2

        points = await _scroll_news_v2(limit=_NEWS_SCROLL_WINDOW)
        if not points:
            return None
        articles: list[dict[str, Any]] = []
        for p in points:
            a = _point_to_article(p)
            if not isinstance(a, dict):
                continue
            if not str(a.get("title") or "").strip():
                continue
            articles.append(a)
        if not articles:
            return None

        # Newest first (ISO-8601 string compare; missing published_at sinks).
        articles.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)

        lines: list[str] = []
        for i, a in enumerate(articles[:_NEWS_MAX_ITEMS], start=1):
            title = str(a.get("title") or "").strip()
            source = str(a.get("source") or "").strip() or "?"
            rel = _relative_time(a.get("published_at"))
            rel_str = f" ({rel})" if rel else ""
            url = str(a.get("url") or a.get("source_url") or "").strip()
            lines.append(f"{i}. {title} — {source}{rel_str}")
            if url:
                lines.append(f"   {url}")
        if not lines:
            return None
        return "Top stories:\n" + "\n".join(lines)
    except Exception as e:
        logger.debug("chat_tool_news_failed", error=str(e)[:120])
        return None
