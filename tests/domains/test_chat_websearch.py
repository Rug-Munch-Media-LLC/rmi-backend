"""Unit tests for SearXNG web-search fallback in the chat brain.

Covers ``app/core/web_search.py`` + the web-routing in ``app/routers/chat.py``:
  - ``searxng_search``: result mapping, fail-open on network/non-200/bad JSON,
    Redis caching is best-effort.
  - Routing contract (own-data-first + current-events only): RAG hit +
    non-current-events query → NO web call; RAG miss + non-current-events
    query → NO web call either (web search is reserved for news-y queries);
    current-events keyword → web call even with RAG hits; web search
    down/empty → chat still answers gracefully.
  - Header contract: ``X-Web-Context: N`` set only when web results were
    included; ``X-RAG-Context`` unchanged.
  - Date awareness: the system prompt always carries the current UTC
    date/year plus the "use web search results" instruction.
  - Own-data-first ordering: "RAG Context:" block precedes the
    "Web results (live):" block in the assembled system prompt.

No network I/O: httpx and Redis are mocked at the module boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import web_search
from app.routers import chat as chat_mod
from app.routers.chat import (
    _format_web_context,
    _looks_like_current_events,
    router as chat_router,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(chat_router)
    with TestClient(app) as c:
        yield c


def _completion_result(content: str = "Grounded answer.") -> dict[str, Any]:
    return {"content": content, "model": "test-model", "_provider": "test-provider", "_latency_ms": 1, "usage": {}}


def _searxng_payload(results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"results": results if results is not None else []}


# ══════════════════════════════════════════════════════════════════════
# searxng_search — fail-open unit tests (httpx + redis mocked)
# ══════════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """httpx.AsyncClient class fake — configure via class attrs before use."""

    _status: ClassVar[int] = 200
    _payload: ClassVar[dict[str, Any]] = {}
    _raise: ClassVar[Exception | None] = None
    captured: ClassVar[dict[str, Any]] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeClient.captured = {"url": url, "params": params}
        if _FakeClient._raise is not None:
            raise _FakeClient._raise
        return _FakeResp(_FakeClient._status, _FakeClient._payload)


def _patch_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeClient)


def _patch_redis_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the Redis cache entirely so tests are hermetic."""
    monkeypatch.setattr("app.core.redis.get_redis_async", lambda: None)


@pytest.mark.asyncio
async def test_searxng_search_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    _FakeClient._status = 200
    _FakeClient._raise = None
    _FakeClient._payload = _searxng_payload(
        [
            {"title": "Bitcoin rallies on Fed news", "url": "https://news.example.com/1", "content": "Fed cut rates."},
            {"title": "Iran tensions rise", "url": "https://news.example.com/2", "content": "Oil spiked 5%."},
            {"title": "", "url": "https://news.example.com/3", "content": "No title but a URL"},
            {"title": "Dropped", "url": "", "content": ""},  # no title AND no url → skipped
        ]
    )
    results = await web_search.searxng_search("fed iran latest", max_results=3)
    assert results == [
        {"title": "Bitcoin rallies on Fed news", "url": "https://news.example.com/1", "content": "Fed cut rates."},
        {"title": "Iran tensions rise", "url": "https://news.example.com/2", "content": "Oil spiked 5%."},
        {"title": "", "url": "https://news.example.com/3", "content": "No title but a URL"},
    ]
    assert _FakeClient.captured["url"] == f"{web_search._searxng_url()}/search"
    assert _FakeClient.captured["params"] == {"q": "fed iran latest", "format": "json"}


@pytest.mark.asyncio
async def test_searxng_search_respects_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    _FakeClient._status = 200
    _FakeClient._raise = None
    _FakeClient._payload = _searxng_payload(
        [{"title": f"hit {i}", "url": f"https://e/{i}", "content": "c"} for i in range(10)]
    )
    results = await web_search.searxng_search("query", max_results=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_searxng_search_fail_open_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    _FakeClient._raise = OSError("searxng down")
    assert await web_search.searxng_search("anything") == []


@pytest.mark.asyncio
async def test_searxng_search_fail_open_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    _FakeClient._status = 503
    _FakeClient._raise = None
    _FakeClient._payload = {}
    assert await web_search.searxng_search("anything") == []


@pytest.mark.asyncio
async def test_searxng_search_fail_open_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    _FakeClient._status = 200
    _FakeClient._raise = None
    _FakeClient._payload = {"unexpected": "shape"}
    assert await web_search.searxng_search("anything") == []


@pytest.mark.asyncio
async def test_searxng_search_empty_query_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis_off(monkeypatch)
    _patch_httpx(monkeypatch)
    assert await web_search.searxng_search("   ") == []
    assert await web_search.searxng_search("") == []


# ══════════════════════════════════════════════════════════════════════
# Heuristics + formatter
# ══════════════════════════════════════════════════════════════════════


def test_looks_like_current_events_keywords() -> None:
    assert _looks_like_current_events("what's the latest news on iran?") is True
    assert _looks_like_current_events("did the fed cut rates today?") is True
    assert _looks_like_current_events("breaking: election results") is True
    assert _looks_like_current_events("tell me about this week's war coverage") is True
    assert _looks_like_current_events("just now btc pumped") is True


def test_looks_like_current_events_negative() -> None:
    assert _looks_like_current_events("how do honeypots work?") is False
    assert _looks_like_current_events("is 0xaaaa a rug?") is False
    # word-boundary: "fed" must not match inside "coffee" / "federal"
    assert _looks_like_current_events("where does coffee come from") is False
    assert _looks_like_current_events("federal reserve history") is False


def test_format_web_context_labels_results() -> None:
    results = [
        {"title": "First hit", "url": "https://a.example/1", "content": "snippet one"},
        {"title": "Second hit", "url": "https://b.example/2", "content": ""},
    ]
    text = _format_web_context(results)
    assert "[1] First hit" in text
    assert "https://a.example/1" in text
    assert "snippet one" in text
    assert "[2] Second hit" in text
    assert "https://b.example/2" in text


# ══════════════════════════════════════════════════════════════════════
# Routing — own-data-first (RAG before web, skip web on RAG hit)
# ══════════════════════════════════════════════════════════════════════


def _rag_hits(n: int) -> tuple[str, int]:
    hits = [
        {
            "content": f"RAG intel hit {i} about reentrancy protections",
            "score": 0.9 - i * 0.1,
            "metadata": {},
            "collection": "threat_intel",
            "doc_id": f"doc-{i}",
        }
        for i in range(n)
    ]
    return chat_mod._format_rag_context(hits), len(hits)


def test_rag_hit_skips_web_search(client: TestClient) -> None:
    """RAG hit + non-current-events query → NO web call, no X-Web-Context."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    rag_context, rag_count = _rag_hits(2)
    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=(rag_context, rag_count))),
        patch.object(chat_mod, "searxng_search", new=AsyncMock(return_value=[])) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "explain reentrancy protections"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "2"
    assert resp.headers.get("X-Web-Context") is None
    web_mock.assert_not_awaited()
    assert "RAG Context:" in captured["messages"][0]["content"]
    assert "Web results (live):" not in captured["messages"][0]["content"]


def test_rag_miss_plain_query_skips_web_search(client: TestClient) -> None:
    """RAG miss (plain, non-current-events query) → NO web search.

    Web search is reserved for news-y queries. A RAG miss on a definition /
    identity question must never fire SearXNG — that previously leaked
    irrelevant numbered search-result lists into the answer (the "Genius
    lyrics" bug), so no X-Web-Context and no web call here.
    """
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(
            chat_mod,
            "searxng_search",
            new=AsyncMock(return_value=[{"title": "Web hit", "url": "https://w.example/1", "content": "live snippet"}]),
        ) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "what is the meaning of life"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.headers.get("X-Web-Context") is None
    web_mock.assert_not_awaited()
    system_msg = captured["messages"][0]["content"]
    assert "Web results (live):" not in system_msg
    assert "https://w.example/1" not in system_msg


def test_rag_miss_current_events_triggers_web_search(client: TestClient) -> None:
    """RAG miss + newsy query → web results included, header set."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(
            chat_mod,
            "searxng_search",
            new=AsyncMock(
                return_value=[
                    {"title": "Fed decision", "url": "https://news.example.com/fed", "content": "Rates held."},
                    {"title": "Iran update", "url": "https://news.example.com/iran", "content": "Talks stalled."},
                ]
            ),
        ) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "what's the latest on iran?"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.headers.get("X-Web-Context") == "2"
    web_mock.assert_awaited_once()
    system_msg = captured["messages"][0]["content"]
    assert "Web results (live):" in system_msg
    assert "Fed decision" in system_msg
    assert "https://news.example.com/iran" in system_msg


def test_current_events_keyword_triggers_web_even_with_rag_hit(client: TestClient) -> None:
    """Newsy query → web search runs even when RAG returned hits (OR clause)."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    rag_context, rag_count = _rag_hits(1)
    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=(rag_context, rag_count))),
        patch.object(
            chat_mod,
            "searxng_search",
            new=AsyncMock(return_value=[{"title": "Breaking news", "url": "https://n.example/x", "content": "live"}]),
        ) as web_mock,
    ):
        resp = client.post("/api/v1/chat", json={"message": "breaking news on the fed today"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "1"
    assert resp.headers.get("X-Web-Context") == "1"
    web_mock.assert_awaited_once()
    system_msg = captured["messages"][0]["content"]
    # Own data first: RAG block must precede the web block.
    assert system_msg.index("RAG Context:") < system_msg.index("Web results (live):")


def test_web_down_graceful(client: TestClient) -> None:
    """SearXNG raising must never break the chat response."""

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("Still answers.")

    async def _web_down(query: str, max_results: int = 5) -> list[dict[str, str]]:
        raise OSError("searxng unreachable")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(chat_mod, "searxng_search", new=AsyncMock(side_effect=_web_down)),
    ):
        resp = client.post("/api/v1/chat", json={"message": "what happened in the war today?"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.headers.get("X-Web-Context") is None
    assert resp.json()["message"] == "Still answers."


def test_web_empty_graceful(client: TestClient) -> None:
    """SearXNG returning no results → chat proceeds without X-Web-Context."""

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("No web results needed.")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(chat_mod, "searxng_search", new=AsyncMock(return_value=[])),
    ):
        resp = client.post("/api/v1/chat", json={"message": "latest news"})

    assert resp.status_code == 200
    assert resp.headers.get("X-Web-Context") is None
    assert resp.json()["message"] == "No web results needed."


def test_client_context_override_skips_server_web_search(client: TestClient) -> None:
    """Telegram-bot style context_override → no server-side RAG OR web search."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result("Bot-grounded answer.")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock()) as rag_mock,
        patch.object(chat_mod, "searxng_search", new=AsyncMock()) as web_mock,
    ):
        resp = client.post(
            "/api/v1/chat",
            json={"message": "latest election news", "context_override": {"system_context": "bot-provided context"}},
        )

    assert resp.status_code == 200
    rag_mock.assert_not_awaited()
    web_mock.assert_not_awaited()
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.headers.get("X-Web-Context") is None


# ══════════════════════════════════════════════════════════════════════
# Date awareness — injected into every chat call
# ══════════════════════════════════════════════════════════════════════


def test_system_prompt_has_date_awareness_and_year(client: TestClient) -> None:
    captured: dict[str, Any] = {}
    now = datetime.now(UTC)
    # Day-granularity ONLY (per-minute stamps broke provider prefix caching —
    # the llama audit flagged them as the prefix-cache killer). The date is a
    # stable-for-the-day session header, never a per-message timestamp.
    expected_date = now.strftime("%Y-%m-%d")

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(chat_mod, "searxng_search", new=AsyncMock(return_value=[])),
    ):
        resp = client.post("/api/v1/chat", json={"message": "hello"})

    assert resp.status_code == 200
    system_msg = captured["messages"][0]["content"]
    assert f"Current date: {expected_date}" in system_msg
    assert f"(year {now.year})" in system_msg
    # No per-minute granularity anywhere in the system prompt (prefix-cache killer).
    assert f" {now.strftime('%H:%M')} UTC" not in system_msg
    assert "You know today's date." in system_msg
    assert "use the web search results provided" in system_msg


def test_system_prompt_has_date_awareness_in_stream(client: TestClient) -> None:
    """The stream endpoint shares the date-aware system prompt (via _build_messages)
    and keeps working after the change (no X-Web-Context — stream has no web search)."""

    async def _fake_stream(messages: Any, **kwargs: Any) -> Any:
        # The system prompt reaches the LLM through _build_messages — assert
        # the date-awareness instruction is present on every stream call.
        assert "(year " in messages[0]["content"]
        assert "You know today's date." in messages[0]["content"]
        yield "streamed"

    with (
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(return_value=("", 0))),
        patch.object(chat_mod, "do_stream_chat_completion", side_effect=_fake_stream),
    ):
        resp = client.post("/api/v1/chat/stream", json={"message": "any message"})

    assert resp.status_code == 200
    assert resp.headers.get("X-Web-Context") is None
    assert "streamed" in resp.text
