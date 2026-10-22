"""Contract tests for the canonical chat endpoint (POST /api/v1/chat).

Retrieval and the model are mocked: every test proves the *shape* the website
widget and the bot code against, plus the never-stop degradation paths.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.domains.chat import routes, service
from app.domains.chat.mount import router as chat_router
from app.domains.chat.ports import ModelResult
from app.domains.chat.retrieval import Passage

QUERY = {"message": "what does the scanner say about this token?", "channel": "web"}
PASSAGES = [
    Passage("Scanner verdict: high risk, mint authority live.", "Scanner docs",
            "https://rugmunch.io/docs/scanner", "codebase_reference_v2_8b", "rag", 0.91),
    Passage("Honeypot simulation fails on sell.", "Honeypot report",
            "https://rugmunch.io/reports/honeypot", "defi_exploits_8b", "rag", 0.77),
]


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router)
    return app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as ac:
        yield ac


def _mock_evidence(monkeypatch, passages=PASSAGES, web=()):
    async def _retrieve(_query, top_k=6):
        return list(passages)

    async def _web(_query):
        return list(web)

    monkeypatch.setattr(service, "retrieve", _retrieve)
    monkeypatch.setattr(service, "search_and_extract", _web)


def _mock_model(monkeypatch, result):
    async def _complete(_messages):
        return result

    monkeypatch.setattr(service.ports, "complete", _complete)


async def test_contract_fields_and_citation_mapping(client, monkeypatch):
    _mock_evidence(monkeypatch)
    _mock_model(monkeypatch, ModelResult("High risk: mint authority is live [1].", "openrouter", "qwen3"))

    body = (await client.post("/api/v1/chat", json=QUERY)).json()

    assert {"answer", "message", "citations", "provider", "model", "fallback_used",
            "refused", "latency_ms"} <= set(body)
    assert body["answer"] == body["message"]
    assert body["provider"] == "openrouter"
    assert body["model"] == "qwen3"
    assert body["fallback_used"] is False
    assert body["refused"] is False
    assert isinstance(body["latency_ms"], int)
    assert [c["n"] for c in body["citations"]] == [1, 2]
    assert body["citations"][0] == {
        "n": 1, "title": "Scanner docs", "url": "https://rugmunch.io/docs/scanner",
        "source": "rag", "library_id": "codebase_reference_v2_8b", "score": 0.91,
    }


async def test_request_defaults_accept_channel_and_ids(client, monkeypatch):
    _mock_evidence(monkeypatch)
    _mock_model(monkeypatch, ModelResult("Grounded answer [1].", "nvidia", "kimi-k3"))

    resp = await client.post("/api/v1/chat", json={
        "message": "how does the honeypot check work?",
        "persona": "security", "channel": "telegram",
        "conversation_id": "c-1", "user_id": "u-1",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"].endswith("[1].")
    assert body["citations"][0]["n"] == 1


async def test_refusal_when_no_evidence(client, monkeypatch):
    _mock_evidence(monkeypatch, passages=[], web=[])
    _mock_model(monkeypatch, ModelResult("never called", "x", None))

    resp = await client.post("/api/v1/chat", json={"message": "what is the SEC ruling?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["refused"] is True
    assert body["refusal_reason"] == "insufficient_evidence"
    assert body["citations"] == []
    assert body["provider"] == "retrieval_only"


async def test_model_failure_degrades_to_retrieval_with_citations(client, monkeypatch):
    _mock_evidence(monkeypatch)
    _mock_model(monkeypatch, None)

    resp = await client.post("/api/v1/chat", json=QUERY)

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "retrieval_only"
    assert body["fallback_used"] is True
    assert body["refused"] is False
    assert [c["n"] for c in body["citations"]] == [1, 2]
    assert body["message"] == body["answer"]


async def test_web_fallback_marks_fallback_used(client, monkeypatch):
    web = [Passage("SEC approved the spot rule change on 2026-10-20.", "SEC filing",
                   "https://sec.gov/filing", None, "web", 1.0)]
    _mock_evidence(monkeypatch, passages=[], web=web)
    _mock_model(monkeypatch, ModelResult("The SEC approved it [1].", "groq", "gpt-oss-120b"))

    body = (await client.post("/api/v1/chat", json={"message": "latest SEC news"})).json()

    assert body["fallback_used"] is True
    assert body["citations"] == [{"n": 1, "title": "SEC filing", "url": "https://sec.gov/filing",
                                  "source": "web", "library_id": None, "score": 1.0}]


async def test_rate_limit_returns_429(client, monkeypatch):
    _mock_evidence(monkeypatch)
    _mock_model(monkeypatch, ModelResult("ok [1].", "local", None))
    monkeypatch.setattr(routes._limiter, "per_minute", 1)
    monkeypatch.setattr(routes._limiter, "_redis_failed", True)
    routes._limiter._memory.clear()
    payload = {"message": "hello", "user_id": "rate-test-user"}

    first = await client.post("/api/v1/chat", json=payload)
    second = await client.post("/api/v1/chat", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["retry-after"]
    assert second.json()["detail"]["error"] == "rate_limit_exceeded"


async def test_legacy_handler_reachable_under_alias(client):
    """Registration proof: an invalid body reaches the legacy handler (422)."""
    resp = await client.post("/api/v1/chat/legacy", json={"nope": 1})
    assert resp.status_code not in (404, 405)
