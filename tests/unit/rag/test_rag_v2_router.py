"""Unit tests for the RAG v2 router (``app.routers.rag_v2``).

Exercises the agentic ``/ask`` and ``/evaluate`` endpoints through a minimal
FastAPI app (router only) via TestClient:

  1. ``/ask`` returns the graph's answer.
  2. ``/ask`` passes sources through (sentinel stripped).
  3. ``/ask`` sets ``verified`` from the unverified sentinel.
  4. ``/evaluate`` returns metrics and records them.
  5. ``/evaluate`` forwards a custom test set / collection.
  6. ``/ask`` returns 429 once the per-client rate limit is exhausted.

The compiled LangGraph is replaced with an AsyncMock — no graph, LLM, or
network runs. The rate-limit dependency runs through the real
UnifiedRateLimiter singleton (in-memory fallback in this environment);
every test uses a unique ``X-API-Key`` so the per-key token buckets never
bleed across tests.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "a" * 32)
os.environ.setdefault("ENVIRONMENT", "dev")

from app.routers.rag_v2 import router as rag_v2_router

# The graph marks answers without citations with this sentinel; the router
# must strip it from sources and set verified=False.
_UNVERIFIED = "__unverified__"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(rag_v2_router)
    with TestClient(app) as c:
        yield c


def _ask_headers() -> dict[str, str]:
    """Unique API key per call → each test gets a fresh rate-limit bucket."""
    return {"X-API-Key": f"ask-test-{uuid.uuid4().hex}"}


@contextmanager
def _mocked_graph(result: dict):
    """Replace the cached compiled graph with a fake whose ainvoke returns `result`."""
    graph = AsyncMock()
    graph.ainvoke.return_value = result
    with patch("app.routers.rag_v2.get_rag_graph", return_value=graph) as patcher:
        yield graph, patcher


# ── /ask ───────────────────────────────────────────────────────────────


def test_ask_endpoint_returns_answer(client: TestClient) -> None:
    graph_result = {
        "answer": "This token shows honeypot patterns.",
        "query_type": "security",
        "sources": ["scams"],
        "tool_calls": [{"tool": "scan_token", "result": {"risk": "high"}}],
    }
    with _mocked_graph(graph_result) as (graph, _):
        resp = client.post("/api/v1/rag/v2/ask", json={"query": "is this a honeypot?"}, headers=_ask_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "This token shows honeypot patterns."
    assert body["query_type"] == "security"
    # The graph was invoked with the exact query from the request body.
    graph.ainvoke.assert_awaited_once_with({"query": "is this a honeypot?"})


def test_ask_endpoint_returns_sources(client: TestClient) -> None:
    graph_result = {
        "answer": "Multiple sources agree.",
        "query_type": "general",
        "sources": ["scams", "news"],
        "tool_calls": [],
    }
    with _mocked_graph(graph_result):
        resp = client.post("/api/v1/rag/v2/ask", json={"query": "q"}, headers=_ask_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == ["scams", "news"]


def test_ask_endpoint_returns_verified_flag(client: TestClient) -> None:
    # Real citations → verified=True.
    with _mocked_graph({"answer": "a", "query_type": "general", "sources": ["scams"], "tool_calls": []}):
        resp = client.post("/api/v1/rag/v2/ask", json={"query": "q"}, headers=_ask_headers())
    assert resp.json()["verified"] is True

    # Unverified sentinel → verified=False and the sentinel is stripped
    # from the exposed sources list.
    with _mocked_graph({"answer": "a", "query_type": "general", "sources": [_UNVERIFIED], "tool_calls": []}):
        resp = client.post("/api/v1/rag/v2/ask", json={"query": "q"}, headers=_ask_headers())
    body = resp.json()
    assert body["verified"] is False
    assert body["sources"] == []


def test_ask_endpoint_tool_calls_passthrough(client: TestClient) -> None:
    graph_result = {
        "answer": "a",
        "query_type": "market",
        "sources": ["news"],
        "tool_calls": [{"tool": "get_token_price", "result": {"price_usd": 150.0}}],
    }
    with _mocked_graph(graph_result):
        resp = client.post("/api/v1/rag/v2/ask", json={"query": "SOL price"}, headers=_ask_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["tool_calls"] == [{"tool": "get_token_price", "result": {"price_usd": 150.0}}]


# ── /evaluate ──────────────────────────────────────────────────────────


def test_evaluate_endpoint_returns_metrics(client: TestClient) -> None:
    metrics = {
        "faithfulness": 0.91,
        "answer_relevancy": 0.87,
        "context_precision": 0.80,
        "context_recall": 0.75,
    }
    eval_result = {
        "metrics": metrics,
        "metric_source": "heuristics",
        "timestamp": "2026-08-03T00:00:00Z",
        "collection": "threat_intel_v2",
        "test_count": 3,
    }
    mock_eval = AsyncMock(return_value=eval_result)
    record = Mock()

    with (
        patch("app.rag.evaluation.evaluate_rag_pipeline", mock_eval),
        patch("app.rag.evaluation.record_evaluation_results", record),
    ):
        resp = client.post("/api/v1/rag/v2/evaluate", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["metrics"] == metrics
    assert body["test_count"] == 3
    # Default collection applied when the request omits it.
    assert mock_eval.await_args.kwargs["collection"] == "threat_intel_v2"
    assert mock_eval.await_args.kwargs["test_set"] is None
    # Results are persisted for trending.
    record.assert_called_once_with(body)


def test_evaluate_endpoint_with_test_set(client: TestClient) -> None:
    test_set = [{"question": "is this token a rugpull?", "ground_truth": "no"}]
    mock_eval = AsyncMock(
        return_value={
            "metrics": {"faithfulness": 1.0},
            "test_count": 1,
            "collection": "threat_intel_v2",
            "timestamp": "2026-08-03T00:00:00Z",
        }
    )

    # Custom test set passes through; collection defaults.
    with (
        patch("app.rag.evaluation.evaluate_rag_pipeline", mock_eval),
        patch("app.rag.evaluation.record_evaluation_results", Mock()),
    ):
        resp = client.post("/api/v1/rag/v2/evaluate", json={"test_set": test_set})

    assert resp.status_code == 200
    assert mock_eval.await_args.kwargs["test_set"] == test_set
    assert mock_eval.await_args.kwargs["collection"] == "threat_intel_v2"

    # Explicit collection overrides the default.
    with (
        patch("app.rag.evaluation.evaluate_rag_pipeline", mock_eval),
        patch("app.rag.evaluation.record_evaluation_results", Mock()),
    ):
        resp = client.post("/api/v1/rag/v2/evaluate", json={"collection": "custom_v1"})

    assert resp.status_code == 200
    assert mock_eval.await_args.kwargs["collection"] == "custom_v1"
    assert mock_eval.await_args.kwargs["test_set"] is None


# ── Rate limiting ──────────────────────────────────────────────────────


def test_ask_rate_limited(client: TestClient) -> None:
    """The 4th /ask from the same client is rejected with 429.

    The ``ai`` rate limit is 10 rpm with burst 3, so three requests pass and
    the fourth is rate-limited (in-memory token bucket in this environment).
    """
    headers = _ask_headers()  # one client identity for all four requests
    graph_result = {"answer": "a", "query_type": "general", "sources": ["scams"], "tool_calls": []}

    with _mocked_graph(graph_result):
        statuses = []
        for _ in range(4):
            resp = client.post("/api/v1/rag/v2/ask", json={"query": "q"}, headers=headers)
            statuses.append(resp.status_code)

    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429
    assert "Rate limit exceeded" in resp.json()["detail"]
    assert "Retry-After" in resp.headers
