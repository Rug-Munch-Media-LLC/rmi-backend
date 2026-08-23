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
    """Default: rugbrain search returns one high-score hit.

    2026-08-23: the v2 router calls rugbrain_search directly
    (app/api/v1/rag/search.py) — the old engine_three_pillar_search
    indirection is gone. Patch the name the ROUTER uses.
    """
    import app.api.v1.rag.search as rag_router

    async def fake_search(query, collections=None, limit=10, rerank=False):
        return [
            {
                "content": "Honeypot token: cannot sell after purchase",
                "rrf_score": 0.92,
                "metadata": {"category": "honeypot"},
                "doc_id": "doc-1",
            }
        ]

    monkeypatch.setattr(rag_router, "rugbrain_search", fake_search)
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
        assert len(body["hits"]) == 1
        hit = body["hits"][0]
        assert hit["content"] == "Honeypot token: cannot sell after purchase"
        assert hit["score"] == 0.92
        assert hit["collection"] == "scam_intel"
        assert body["collections_searched"] == ["scam_intel"]

    def test_search_unknown_collection_graceful(self, client: TestClient, monkeypatch) -> None:
        """Unknown collection → 200 with empty hits, not 500."""
        import app.api.v1.rag.search as rag_router

        async def fake_search(query, collections=None, limit=10, rerank=False):
            return []

        monkeypatch.setattr(rag_router, "rugbrain_search", fake_search)
        resp = client.post(
            "/api/v1/rag/v2/search",
            json={"query": "anything", "collection": "does_not_exist", "top_k": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
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
        """GET /api/v1/rag/v2/stats → per-collection point/vector counts.

        2026-08-23: the endpoint queries Qdrant v2's HTTP API directly
        (httpx inside the handler) — the engine_get_stats indirection is
        gone. Mock the httpx transport and assert the new response shape:
        {collections: {name: {points, vectors, dim}}, status}.
        """
        import httpx

        import app.api.v1.rag.search as rag_router

        class FakeResp:
            def __init__(self, payload):
                self._p = payload
                self.status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return self._p

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                if url.endswith("/collections"):
                    return FakeResp({"result": {"collections": [{"name": "scam_intel"}, {"name": "known_scams"}]}})
                if url.endswith("/scam_intel"):
                    return FakeResp(
                        {
                            "result": {
                                "points_count": 1000,
                                "vectors_count": 1000,
                                "config": {"params": {"vectors": {"size": 1024}}},
                            }
                        }
                    )
                return FakeResp(
                    {
                        "result": {
                            "points_count": 234,
                            "vectors_count": 234,
                            "config": {"params": {"vectors": {"size": 1024}}},
                        }
                    }
                )

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        client = TestClient(app)
        resp = client.get("/api/v1/rag/v2/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert set(body["collections"]) == {"scam_intel", "known_scams"}
        assert body["collections"]["scam_intel"]["points"] == 1000
        assert body["collections"]["scam_intel"]["dim"] == 1024


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
        """Authenticated ingest returns doc_id + status."""
        import app.api.v1.rag.search as rag_router

        async def fake_ingest(content, collection="default", doc_id=None, metadata=None, **kw):
            return True

        monkeypatch.setattr(rag_router, "rugbrain_ingest", fake_ingest)
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


class TestRagFeedback:
    def test_feedback_scam_ingest(self, app, mock_engine_search, monkeypatch) -> None:
        """Feedback records a known scam into the known_scams collection."""
        import app.api.v1.rag.search as rag_router

        async def fake_ingest(content, collection="default", doc_id=None, metadata=None, **kw):
            return True

        monkeypatch.setattr(rag_router, "rugbrain_ingest", fake_ingest)
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
