"""Tests for the RAG HTTP facade — app/api/v1/rag/search.py (+ RAGService).

Covers:
  - POST /api/v1/rag/v2/search — hits with relevance scores, graceful
    empty results for unknown collections, 422 for empty query
  - GET /api/v1/rag/v2/stats — collection counts + backend
  - POST /api/v1/rag/v2/ingest — document ingest (protected: X-API-Key)
  - POST /api/v1/rag/v2/feedback — scam feedback (protected: X-API-Key)

The v3 engine boundary (``app.rag.service.engine_*``) is mocked so no
Qdrant/ClickHouse/embedding service is touched. Search/stats are public;
ingest/feedback live under PROTECTED_SUBPATHS and require the shared secret.
"""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["RMI_INTERNAL_KEY"] = "test-internal-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32
os.environ["CERTSTREAM_ENABLED"] = "false"
os.environ["WEBHOOK_ENGINE_ENABLED"] = "false"

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

API_KEY = "test-auth-token-32-chars-long!!"


@pytest.fixture(scope="module")
def app():
    """Module-scoped app. TestClient without context manager skips lifespan."""
    from app.factory import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _allow_global_rate_limits():
    """Open the unified rate limiter so the middleware never 429s tests."""
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def mock_engine_search(monkeypatch):
    """Default: engine search returns one high-score hit."""
    import app.rag.service as service

    async def fake_search(query, collection="scam_intel", top_k=5, min_similarity=0.0, filters=None):
        return [
            {
                "content": "Honeypot token: cannot sell after purchase",
                "score": 0.92,
                "metadata": {"category": "honeypot"},
                "collection": collection,
                "id": "doc-1",
            }
        ]

    monkeypatch.setattr(service, "engine_three_pillar_search", fake_search)
    return fake_search


@pytest.fixture
def client(app, mock_engine_search):
    return TestClient(app)


class TestRagSearch:
    def test_search_returns_hits_with_scores(self, client: TestClient) -> None:
        """POST /api/v1/rag/v2/search → hits with content + score."""
        resp = client.post(
            "/api/v1/rag/v2/search",
            json={"query": "honeypot token", "collection": "scam_intel", "top_k": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == "honeypot token"
        assert body["total"] == 1
        assert len(body["hits"]) == 1
        hit = body["hits"][0]
        assert hit["content"] == "Honeypot token: cannot sell after purchase"
        assert hit["score"] == 0.92
        assert hit["collection"] == "scam_intel"
        assert body["collection"] == "scam_intel"

    def test_search_unknown_collection_graceful(self, client: TestClient, monkeypatch) -> None:
        """Unknown collection → 200 with empty hits, not 500."""
        import app.rag.service as service

        async def fake_search(query, collection="scam_intel", top_k=5, min_similarity=0.0, filters=None):
            return []

        monkeypatch.setattr(service, "engine_three_pillar_search", fake_search)
        resp = client.post(
            "/api/v1/rag/v2/search",
            json={"query": "anything", "collection": "does_not_exist", "top_k": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 0
        assert body["hits"] == []

    def test_search_empty_query_422(self, client: TestClient) -> None:
        """Empty (whitespace-only) query violates min_length after strip → 422."""
        resp = client.post(
            "/api/v1/rag/v2/search",
            json={"query": "   ", "collection": "scam_intel"},
        )
        assert resp.status_code == 422, resp.text

    def test_search_bad_top_k_422(self, client: TestClient) -> None:
        """top_k=0 is out of the 1..50 range → 422."""
        resp = client.post(
            "/api/v1/rag/v2/search",
            json={"query": "honeypot", "top_k": 0},
        )
        assert resp.status_code == 422, resp.text


class TestRagStats:
    def test_stats_shape(self, app, monkeypatch) -> None:
        """GET /api/v1/rag/v2/stats → collection counts + embedder backend.

        The router calls ``engine_get_stats()`` synchronously (the engine
        function is sync — ``app/rag/engine.py::get_stats``), so the fake
        must be a plain function, not a coroutine. Patching is done on the
        router's own namespace (``app.api.v1.rag.search.engine_get_stats``)
        because the router imports ``get_stats as engine_get_stats``.
        """
        import app.api.v1.rag.search as rag_router

        def fake_stats(collections=None):
            return {
                "total_docs": 1234,
                "backend": "qdrant",
                "collections": [
                    {"name": "scam_intel", "docs": 1000},
                    {"name": "known_scams", "docs": 234},
                ],
            }

        monkeypatch.setattr(rag_router, "engine_get_stats", fake_stats)
        client = TestClient(app)
        resp = client.get("/api/v1/rag/v2/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_docs"] == 1234
        assert body["backend"] == "qdrant"
        assert len(body["collections"]) == 2


class TestRagIngest:
    def test_ingest_requires_auth(self, app, mock_engine_search) -> None:
        """/ingest is a PROTECTED_SUBPATH — no key → 401."""
        client = TestClient(app)
        resp = client.post(
            "/api/v1/rag/v2/ingest",
            json={"collection": "scam_intel", "content": "test doc"},
        )
        assert resp.status_code == 401, resp.text

    def test_ingest_document(self, app, mock_engine_search, monkeypatch) -> None:
        """Authenticated ingest returns doc_id + status + chunks."""
        import app.rag.service as service

        async def fake_ingest(collection, doc_id, content, metadata=None, content_type="general"):
            return {"status": "ok", "chunks": 3}

        monkeypatch.setattr(service, "engine_ingest_document", fake_ingest)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/rag/v2/ingest",
            json={"collection": "scam_intel", "content": "Known scam address 0xabc", "doc_id": "doc-42"},
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["doc_id"] == "doc-42"
        assert body["collection"] == "scam_intel"
        assert body["status"] == "ok"
        assert body["chunks"] == 3


class TestRagFeedback:
    def test_feedback_scam_ingest(self, app, mock_engine_search, monkeypatch) -> None:
        """Feedback records a known scam into the known_scams collection."""
        import app.rag.service as service

        async def fake_ingest(collection, doc_id, content, metadata=None, content_type="general"):
            return {"status": "ok", "chunks": 1}

        monkeypatch.setattr(service, "engine_ingest_document", fake_ingest)
        client = TestClient(app)
        resp = client.post(
            "/api/v1/rag/v2/feedback",
            json={
                "token_address": "0xScam123",
                "chain": "ethereum",
                "safety_score": 10,
                "risk_flags": ["honeypot_detected"],
                "source": "scanner",
                "action": "scam_confirmed",
            },
            headers={"X-API-Key": API_KEY},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["collection"] == "known_scams"
        assert body["doc_id"] == "0xScam123"
        assert body["status"] == "ok"
