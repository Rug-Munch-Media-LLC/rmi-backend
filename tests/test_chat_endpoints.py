"""Tests for the AI chat API — app/routers/chat.py.

Covers:
  - POST /api/v1/chat (non-streaming): response message, RAG context
    header, LiteLLM error → 502, empty message → 422
  - POST /api/v1/chat/stream (SSE): token streaming
  - Chat rate limiter → 429
  - Message persistence in the SQLite history DB

Everything external is mocked at the module boundary: the LLM completion
(``do_chat_completion`` / ``do_stream_chat_completion``), the chat rate
limiter, and the global unified rate limiter. The SQLite DB is redirected
to a temp file so the real chat history is never touched.
"""

from __future__ import annotations

import os
import sqlite3

os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32
os.environ["CERTSTREAM_ENABLED"] = "false"
os.environ["WEBHOOK_ENGINE_ENABLED"] = "false"

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app():
    """Module-scoped app. TestClient without context manager skips lifespan,
    so no Redis/ClickHouse startup happens.

    The slowapi integration registers a 429 exception handler
    (``_rate_limit_exceeded_handler``) that reads ``request.state.view_rate_limit``
    — a state that is never set for undecorated routes like /api/v1/chat, so any
    HTTPException(429) raised in a handler crashes to 500. We override the 429
    handler here (before the middleware stack is built on first request) to
    isolate the chat rate-limit behavior under test. The slowapi bug is tracked
    separately as a production defect.
    """
    from starlette.responses import JSONResponse

    from app.factory import create_app

    app = create_app()

    def plain_429(request, exc):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    app.add_exception_handler(429, plain_429)
    return app


@pytest.fixture(autouse=True)
def _clear_chat_cache() -> None:
    """The chat response cache is keyed on the message text (see
    ``app.routers.chat._cache_key``). Without clearing it, a repeated message
    across tests short-circuits to a stale cached answer — and a stale
    ``X-RAG-Context`` (e.g. the RAG-header test would see the 0 recorded by
    the earlier plain-completion test instead of the patched count).
    """
    import app.routers.chat as chat_mod

    chat_mod._chat_cache.clear()
    yield
    chat_mod._chat_cache.clear()


@pytest.fixture(autouse=True)
def _allow_global_rate_limits():
    """Open the unified rate limiter so the middleware never 429s tests."""
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    """Redirect the chat SQLite DB to a temp file and create tables.

    The persistence helpers moved to ``app.routers.chat_db`` (extracted from
    the chat router), so the path is patched there — ``_get_db()`` resolves
    the module-global ``DB_PATH`` at call time.
    """
    import app.routers.chat_db as chat_db_mod

    db_path = str(tmp_path / "chat_test.db")
    monkeypatch.setattr(chat_db_mod, "DB_PATH", db_path)
    chat_db_mod._ensure_tables()
    return db_path


@pytest.fixture
def mock_completion(monkeypatch):
    """Fake LLM completion — returns a canned response dict."""
    import app.routers.chat as chat_mod

    async def fake_completion(**kwargs):
        return {
            "content": "The token is trending bullish.",
            "model": "qwen3-coder",
            "_provider": "local",
            "_latency_ms": 42,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(chat_mod, "do_chat_completion", fake_completion)
    return fake_completion


@pytest.fixture
def allow_chat_rate_limit(monkeypatch):
    """Default: chat rate limiter allows every request."""
    import app.routers.chat as chat_mod

    limiter = AsyncMock()
    limiter.allow = AsyncMock(
        return_value=(True, {"X-RateLimit-Limit": 30, "X-RateLimit-Remaining": 29, "X-RateLimit-Reset": 60})
    )
    monkeypatch.setattr(chat_mod, "get_chat_rate_limiter", lambda: limiter)
    return limiter


@pytest.fixture
def client(app, chat_db, mock_completion, allow_chat_rate_limit):
    """TestClient WITHOUT context manager — no lifespan startup."""
    return TestClient(app)


class TestChatEndpoint:
    def test_chat_returns_ai_message(self, client: TestClient) -> None:
        """POST /api/v1/chat → 200 with the mocked completion content."""
        resp = client.post("/api/v1/chat", json={"message": "Is BONK safe?"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["message"] == "The token is trending bullish."
        assert body["model"] == "qwen3-coder"
        assert body["provider"] == "local"
        assert body["id"].startswith("resp-")

    def test_chat_sets_rag_context_header(self, client: TestClient, monkeypatch) -> None:
        """X-RAG-Context header exposes how many RAG hits grounded the answer."""
        import app.routers.chat as chat_mod

        async def fake_rag_context(message: str) -> tuple[str, int]:
            return "RAG hit content about BONK", 2

        monkeypatch.setattr(chat_mod, "_search_rag_context", fake_rag_context)
        resp = client.post("/api/v1/chat", json={"message": "Is BONK safe?"})
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("X-RAG-Context") == "2"

    def test_chat_empty_message_422(self, client: TestClient) -> None:
        """Empty message violates min_length=1 → 422."""
        resp = client.post("/api/v1/chat", json={"message": ""})
        assert resp.status_code == 422, resp.text

    def test_chat_provider_error_502(self, client: TestClient, monkeypatch) -> None:
        """LiteLLM completion returning an error dict → 502 (ExternalAPIError)."""
        import app.routers.chat as chat_mod

        async def failing_completion(**kwargs):
            return {"error": "upstream provider timeout"}

        monkeypatch.setattr(chat_mod, "do_chat_completion", failing_completion)
        resp = client.post("/api/v1/chat", json={"message": "hello"})
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["status"] == 502
        assert body["code"] == "external_api.failed"
        assert body["detail"] == "upstream provider timeout"

    def test_chat_persists_messages(self, client: TestClient, chat_db) -> None:
        """User + assistant messages are written to the SQLite history DB."""
        resp = client.post("/api/v1/chat", json={"message": "What is RugMunch?"})
        assert resp.status_code == 200, resp.text

        conn = sqlite3.connect(chat_db)
        try:
            rows = conn.execute(
                "SELECT role, content FROM chat_messages WHERE user_id = ? ORDER BY created_at",
                ("anon",),
            ).fetchall()
        finally:
            conn.close()

        roles = [r[0] for r in rows]
        assert roles == ["user", "assistant"], f"expected user+assistant, got {roles}"
        assert rows[0][1] == "What is RugMunch?"
        assert rows[1][1] == "The token is trending bullish."


class TestChatRateLimit:
    def test_chat_rate_limited_429(self, app, chat_db, mock_completion, monkeypatch) -> None:
        """Rate limiter rejection → 429 with Retry-After headers.

        The slowapi 429-handler crash (see the module-level ``app`` fixture)
        is avoided by overriding the 429 exception handler at app build time.
        """
        import app.routers.chat as chat_mod

        limiter = AsyncMock()
        limiter.allow = AsyncMock(
            return_value=(False, {"X-RateLimit-Limit": 30, "X-RateLimit-Remaining": 0, "Retry-After": 60})
        )
        monkeypatch.setattr(chat_mod, "get_chat_rate_limiter", lambda: limiter)

        client = TestClient(app)
        resp = client.post("/api/v1/chat", json={"message": "hello"})
        assert resp.status_code == 429, resp.text
        assert "rate" in resp.text.lower()


class TestChatStream:
    def test_stream_yields_sse_tokens(self, app, chat_db, allow_chat_rate_limit, monkeypatch) -> None:
        """POST /api/v1/chat/stream → SSE tokens then a done event."""
        import app.routers.chat as chat_mod

        async def fake_stream(**kwargs):
            yield "Hello "
            yield "world"

        monkeypatch.setattr(chat_mod, "do_stream_chat_completion", fake_stream)

        client = TestClient(app)
        with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}) as resp:
            assert resp.status_code == 200, resp.text
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())

        assert '"type": "token"' in body
        assert '"content": "Hello "' in body
        assert '"content": "world"' in body
        assert '"type": "done"' in body

    def test_stream_sets_rag_context_header(self, app, chat_db, allow_chat_rate_limit, monkeypatch) -> None:
        """Streaming response exposes X-RAG-Context."""
        import app.routers.chat as chat_mod

        async def fake_stream(**kwargs):
            yield "ok"

        async def fake_rag_context(message: str) -> tuple[str, int]:
            return "context", 3

        monkeypatch.setattr(chat_mod, "do_stream_chat_completion", fake_stream)
        monkeypatch.setattr(chat_mod, "_search_rag_context", fake_rag_context)

        client = TestClient(app)
        with client.stream("POST", "/api/v1/chat/stream", json={"message": "hi"}) as resp:
            assert resp.headers.get("X-RAG-Context") == "3"
            resp.read()
