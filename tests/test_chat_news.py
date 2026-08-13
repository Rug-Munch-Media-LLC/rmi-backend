"""Pure unit tests for the chat "news" tool intent.

Covers the news additions to ``app/domains/chat_tools.py``:
  - ``_detect_tool_intent`` → "news" for headline/news phrasing, "market"
    still wins for explicit market phrasing, None for unrelated questions.
  - ``_exec_news`` — renders a compact "Top stories" block (≤10 numbered
    lines, title — source (relative time) + URL) from the Qdrant news_v2
    helpers; fail-open → None on empty/error.
  - ``_run_tool_for_intent`` — routes "news" → ``_exec_news``.

No network, DB, or LLM calls — the Qdrant scroll helper is mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import (
    _detect_tool_intent,
    _exec_news,
    _relative_time,
    _run_tool_for_intent,
)


def _point(
    title: str,
    source: str,
    published_at: str,
    url: str = "https://example.com/article",
) -> dict:
    """A Qdrant news_v2 point in the shape ``_point_to_article`` consumes."""
    return {
        "id": f"news-{title}",
        "payload": {
            "metadata": {
                "news_id": f"news-{title}",
                "title": title,
                "url": url,
                "source": source,
                "published_at": published_at,
                "category": "general",
            },
            "content": "body",
        },
    }


# ── 1. Intent classifier ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "tell me the big news in crypto today",
        "latest news",
        "top stories",
        "show me the headlines",
        "breaking: something happened",
        "what's happening in crypto",
        "what happened today",
    ],
)
def test_detect_tool_intent_returns_news(message: str) -> None:
    assert _detect_tool_intent(message) == "news"


@pytest.mark.parametrize(
    "message",
    [
        "how's the market",
        "fear and greed index",
        "btc eth sol",
    ],
)
def test_detect_tool_intent_market_still_wins(message: str) -> None:
    assert _detect_tool_intent(message) == "market"


def test_detect_tool_intent_market_beats_overlapping_news() -> None:
    # Explicit market phrasing wins even when a news keyword is present.
    assert _detect_tool_intent("latest market news") == "market"


@pytest.mark.parametrize(
    "message",
    [
        "what is a honeypot",
        "hello there",
        "what is bitcoin",
        "subscribe to the newsletter",
    ],
)
def test_detect_tool_intent_none_for_unrelated(message: str) -> None:
    assert _detect_tool_intent(message) is None


def test_detect_tool_intent_price_not_reclassified_as_news() -> None:
    # News is checked AFTER price — a price query stays price.
    assert _detect_tool_intent("what's the latest price of btc") == "price"


# ── 2. Relative time helper ───────────────────────────────────────────


def test_relative_time() -> None:
    assert _relative_time(None) == ""
    assert _relative_time("") == ""
    assert _relative_time("not-a-date") == ""
    now = datetime.now(UTC)
    # Timestamps are offset forward a bit so sub-second test drift never
    # pushes the delta across a rounding boundary (e.g. 4m59s -> "4m ago").
    assert _relative_time((now - timedelta(minutes=5, seconds=30)).isoformat()) == "5m ago"
    assert _relative_time((now - timedelta(hours=3, minutes=1)).isoformat()) == "3h ago"
    assert _relative_time((now - timedelta(days=2, hours=1)).isoformat()) == "2d ago"


# ── 3. _exec_news renders / fail-open ─────────────────────────────────


async def test_exec_news_renders_top_stories() -> None:
    points = [
        _point(
            "Ether hits new high",
            "CoinDesk",
            (datetime.now(UTC) - timedelta(hours=2, minutes=1)).isoformat(),
        ),
        _point(
            "Solana upgrade ships",
            "The Block",
            (datetime.now(UTC) - timedelta(hours=5, minutes=1)).isoformat(),
        ),
        _point(
            "Regulator clarifies rules",
            "Reuters",
            (datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat(),
        ),
    ]
    with mock.patch(
        "app.routers.news._scroll_news_v2",
        new=mock.AsyncMock(return_value=points),
    ):
        text = await _exec_news("latest news")

    assert text is not None
    assert text.startswith("Top stories:\n")
    body = text.split("\n")
    # Header + 2 lines per item (title line + URL line).
    assert len(body) == 1 + 3 * 2
    assert body[1] == "1. Ether hits new high — CoinDesk (2h ago)"
    assert body[2] == "   https://example.com/article"
    assert "3. Regulator clarifies rules — Reuters (1d ago)" in text


async def test_exec_news_sorts_newest_first_and_caps_at_ten() -> None:
    points = [_point(f"Story {i:02d}", f"Source {i:02d}", f"2026-08-{13 - (i % 12):02d}T10:00:00Z") for i in range(20)]
    with mock.patch(
        "app.routers.news._scroll_news_v2",
        new=mock.AsyncMock(return_value=points),
    ):
        text = await _exec_news("top stories")

    assert text is not None
    numbered = [ln for ln in text.split("\n") if ln[:1].isdigit()]
    assert len(numbered) == 10  # capped at 10
    assert numbered[0].startswith("1. Story 00")  # newest published_at first
    assert numbered[-1].startswith("10.")


async def test_exec_news_returns_none_on_empty() -> None:
    with mock.patch(
        "app.routers.news._scroll_news_v2",
        new=mock.AsyncMock(return_value=[]),
    ):
        assert await _exec_news("latest news") is None


async def test_exec_news_returns_none_when_all_titles_empty() -> None:
    point = _point("", "CoinDesk", "2026-08-13T10:00:00Z")
    with mock.patch(
        "app.routers.news._scroll_news_v2",
        new=mock.AsyncMock(return_value=[point]),
    ):
        assert await _exec_news("latest news") is None


async def test_exec_news_fail_open_when_helper_raises() -> None:
    with mock.patch(
        "app.routers.news._scroll_news_v2",
        new=mock.AsyncMock(side_effect=RuntimeError("qdrant down")),
    ):
        assert await _exec_news("latest news") is None


# ── 4. Dispatcher routing ─────────────────────────────────────────────


def test_dispatcher_wires_news_executor() -> None:
    assert chat_tools._EXECUTORS["news"] is _exec_news


async def test_run_tool_for_intent_routes_news_to_exec_news() -> None:
    fake = mock.AsyncMock(return_value="Top stories:\n1. A — B (1h ago)\n   https://x")
    with mock.patch.dict(chat_tools._EXECUTORS, {"news": fake}):
        result = await _run_tool_for_intent("latest news", "news")
    assert result == "Top stories:\n1. A — B (1h ago)\n   https://x"
    fake.assert_awaited_once_with("latest news")


async def test_run_tool_for_intent_unknown_intent_is_none() -> None:
    assert await _run_tool_for_intent("hello", "unknown") is None
