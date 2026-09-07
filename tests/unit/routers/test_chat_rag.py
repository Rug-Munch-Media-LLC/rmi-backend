"""Unit tests for RAG context injection into the web chat endpoint.

Covers ``app/routers/chat.py``:
  - RAG hits are formatted and injected into the system prompt before the
    LLM call (same ``context_override.system_context`` mechanism the Telegram
    bot uses).
  - ``X-RAG-Context: count=N`` response header on both /chat and /chat/stream.
  - Graceful degradation: when RAG search fails/times out, chat still works.
  - Caller-supplied ``context_override.system_context`` skips the server-side
    RAG search (no double-grounding for the Telegram bot).
  - Token-address / $SYMBOL extraction drives the ``scanner_v2`` collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import chat as chat_mod
from app.routers.chat import (
    RAG_COLLECTIONS,
    RAG_TOKEN_COLLECTIONS,
    _build_rag_query,
    _extract_token_hints,
    _format_rag_context,
    _search_rag_context,
    router as chat_router,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


# Every HTTP chat-route test here must be hermetic: fresh allow-all rate
# limiter, empty response cache, per-test SQLite file (see fixture docs in
# tests/conftest.py). The bare-app POSTs would otherwise share the
# ChatRateLimiter singleton + real data/rmi.db across the suite and 429 /
# contend under full-suite ordering (see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("chat_route_hermetic")


@pytest.fixture(autouse=True)
def _no_web_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chat now falls through to SearXNG on RAG miss — keep existing RAG tests
    hermetic by disabling the live web search (no real network to :8089)."""
    monkeypatch.setattr(chat_mod, "searxng_search", AsyncMock(return_value=[]))


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(chat_router)
    with TestClient(app) as c:
        yield c


def _completion_result(content: str = "Grounded answer.") -> dict[str, Any]:
    return {"content": content, "model": "test-model", "_provider": "test-provider", "_latency_ms": 1, "usage": {}}


# ── Token-address / symbol extraction ─────────────────────────────────


def test_extract_token_hints_evm_address_and_symbol() -> None:
    addr = "0x" + "a" * 40
    addresses, symbols = _extract_token_hints(f"Check {addr} and $PEPE and $PEPE again")
    assert addresses == [addr]
    assert symbols == ["PEPE"]  # deduped


def test_extract_token_hints_solana_address() -> None:
    sol = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint (44 chars, base58)
    addresses, symbols = _extract_token_hints(f"is {sol} a honeypot?")
    assert sol in addresses
    assert symbols == []


def test_extract_token_hints_caps_at_three() -> None:
    addrs = [f"0x{'a' * 40}", f"0x{'b' * 40}", f"0x{'c' * 40}", f"0x{'d' * 40}"]
    addresses, _ = _extract_token_hints(" ".join(addrs))
    assert len(addresses) == 3


def test_extract_token_hints_plain_message() -> None:
    addresses, symbols = _extract_token_hints("tell me about recent reentrancy exploits")
    assert addresses == []
    assert symbols == []


def test_build_rag_query_includes_token_hints() -> None:
    addr = "0x" + "b" * 40
    q = _build_rag_query("is this token safe", [addr], ["PEPE"])
    assert addr in q
    assert "$PEPE" in q


def test_format_rag_context_labels_sources() -> None:
    hits = [
        {"content": "alpha content", "score": 0.9, "collection": "threat_intel"},
        {"content": "beta content", "score": 0.8, "collection": "scam_intel"},
    ]
    text = _format_rag_context(hits)
    assert "[1] Source: threat_intel (score: 0.900)" in text
    assert "[2] Source: scam_intel (score: 0.800)" in text
    assert "alpha content" in text
    assert "beta content" in text


# ── RAG consolidation (2026-08-15): focused 5-collection knowledge set ─────


def test_rag_collections_is_focused_five() -> None:
    """RAG_COLLECTIONS is the consolidated focused 5 — not the old 13.

    Every fan-out collection costs one embed + one Qdrant search + one
    mandatory rerank, so the set was pruned to the intent space chat/bot
    actually grounds on. This test pins the exact list so a future
    re-expansion is a deliberate, reviewed change.
    """
    assert len(RAG_COLLECTIONS) == 5
    assert RAG_COLLECTIONS == [
        "security_patterns",
        "codebase_reference",
        "defi_exploits",
        "contract_security",
        "scam_patterns",
    ]
    # The knowledge corpora that anchor the security intent are included.
    assert "codebase_reference" in RAG_COLLECTIONS
    assert "security_patterns" in RAG_COLLECTIONS
    # Dropped (data-class, not knowledge): no live scanner/news/market data.
    for dropped in (
        "threat_intel",
        "scam_intel",
        "news_v2",
        "coding_knowledge",
        "wallet_v2",
        "contract_audits",
        "audit_findings",
    ):
        assert dropped not in RAG_COLLECTIONS, f"{dropped} was dropped in the consolidation"


# ── _search_rag_context orchestration ─────────────────────────────────


@pytest.mark.asyncio
async def test_search_rag_context_searches_base_collections() -> None:
    captured: dict[str, list[str]] = {"collections": []}

    async def _fake_search(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        captured["collections"].append(collection)
        return [
            {
                "content": f"hit in {collection}",
                "score": 0.5,
                "metadata": {},
                "collection": collection,
                "doc_id": "x",
            }
        ]

    with patch.object(chat_mod, "_rag_search_collection", side_effect=_fake_search):
        context, count = await _search_rag_context("tell me about recent reentrancy exploits")

    assert set(captured["collections"]) == set(RAG_COLLECTIONS)
    assert RAG_TOKEN_COLLECTIONS[0] not in captured["collections"]
    assert count == len(RAG_COLLECTIONS)
    assert context  # formatted block non-empty


@pytest.mark.asyncio
async def test_search_rag_context_adds_scanner_v2_for_token_address() -> None:
    captured: dict[str, list[str]] = {"collections": []}

    async def _fake_search(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        captured["collections"].append(collection)
        return [
            {
                "content": f"hit in {collection}",
                "score": 0.5,
                "metadata": {},
                "collection": collection,
                "doc_id": "x",
            }
        ]

    addr = "0x" + "a" * 40
    with patch.object(chat_mod, "_rag_search_collection", side_effect=_fake_search):
        _, count = await _search_rag_context(f"is {addr} a honeypot?")

    assert set(captured["collections"]) == set(RAG_COLLECTIONS + RAG_TOKEN_COLLECTIONS)
    assert count == len(RAG_COLLECTIONS + RAG_TOKEN_COLLECTIONS)


@pytest.mark.asyncio
async def test_search_rag_context_returns_empty_on_failure() -> None:
    async def _boom(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        raise RuntimeError("RAG down")

    with patch.object(chat_mod, "_rag_search_collection", side_effect=_boom):
        context, count = await _search_rag_context("tell me about reentrancy")
    assert context == ""
    assert count == 0


@pytest.mark.asyncio
async def test_search_rag_context_dedupes_and_caps_top_k() -> None:
    async def _fake_search(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        # Identical content across collections must collapse to one hit.
        return [
            {
                "content": "identical content across every collection",
                "score": 0.9,
                "metadata": {},
                "collection": collection,
                "doc_id": collection,
            }
        ]

    with patch.object(chat_mod, "_rag_search_collection", side_effect=_fake_search):
        _, count = await _search_rag_context("dupe test")
    assert count == 1


@pytest.mark.asyncio
async def test_search_rag_context_keeps_verdict_differing_hits_separate() -> None:
    """Dedup must NOT collapse identical text with DIFFERENT verdict labels.

    The labeled good/bad corpora (contract_security verdict=good/bad_example,
    security_patterns verdict=weakness, codebase_reference verdict=clean) can
    return the same snippet text with opposite verdicts — collapsing them
    would silently drop one side of the evidence the LLM needs to weigh
    (secure-reference vs flagged). Same-verdict identical text still dedupes.
    """

    async def _fake_search(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        verdict = {"contract_security": "good", "codebase_reference": "clean"}.get(collection, "weakness")
        return [
            {
                "content": "identical text with differing verdict labels",
                "score": 0.9,
                "metadata": {"verdict": verdict},
                "collection": collection,
                "doc_id": f"doc-{collection}",
            }
        ]

    with patch.object(chat_mod, "_rag_search_collection", side_effect=_fake_search):
        context, count = await _search_rag_context("verdict dedup test")

    # Three verdict labels → three distinct hits (not collapsed to one).
    assert count == 3
    # Each side of the good/bad evidence is visible to the LLM in the
    # rendered context (assert the verdict values, not the label format —
    # the formatter labels hits [collection·category·verdict]).
    for verdict in ("good", "clean", "weakness"):
        assert verdict in context, verdict


@pytest.mark.asyncio
async def test_search_rag_context_dedupes_same_verdict_same_text() -> None:
    """Identical text AND identical verdict collapse to one hit."""

    async def _fake_search(query: str, collection: str, top_k: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "content": "same text same verdict",
                "score": 0.9,
                "metadata": {"verdict": "clean"},
                "collection": collection,
                "doc_id": f"doc-{collection}",
            }
        ]

    with patch.object(chat_mod, "_rag_search_collection", side_effect=_fake_search):
        _, count = await _search_rag_context("same verdict dedup")
    assert count == 1


# ── POST /api/v1/chat ─────────────────────────────────────────────────


def test_chat_injects_rag_context_and_header(client: TestClient) -> None:
    fake_hits = [
        {
            "content": "Reentrancy attacks on protocol X: the attacker called withdraw before updating state.",
            "score": 0.91,
            "metadata": {},
            "collection": "threat_intel",
            "doc_id": "doc-1",
        },
        {
            "content": "Multiple 2026 reentrancy exploits used cross-function calls to drain pools.",
            "score": 0.84,
            "metadata": {},
            "collection": "scam_intel",
            "doc_id": "doc-2",
        },
    ]
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result()

    async def _fake_rag(message: str) -> tuple[str, int]:
        assert message == "Tell me about recent reentrancy exploits"
        return _format_rag_context(fake_hits), len(fake_hits)

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_fake_rag)),
    ):
        resp = client.post("/api/v1/chat", json={"message": "Tell me about recent reentrancy exploits"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "2"
    system_msg = captured["messages"][0]["content"]
    assert "RAG Context:" in system_msg
    assert "Reentrancy attacks on protocol X" in system_msg
    assert "Source: threat_intel" in system_msg


def test_chat_works_without_rag_when_search_returns_empty(client: TestClient) -> None:
    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("Plain answer.")

    async def _fake_rag(message: str) -> tuple[str, int]:
        return "", 0

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_fake_rag)),
    ):
        resp = client.post("/api/v1/chat", json={"message": "hello there"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.json()["message"] == "Plain answer."


def test_chat_degrades_gracefully_when_rag_raises(client: TestClient) -> None:
    """RAG search raising must never break the chat response.

    NB: the message must carry a concrete token referent ($SYMBOL) so the
    vague-question guard (_vague_question, e.g. "this token") does not
    short-circuit with a canned clarification before the RAG search runs.
    """

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("Fallback answer.")

    async def _rag_down(message: str) -> tuple[str, int]:
        raise RuntimeError("RAG service unavailable")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_rag_down)),
        # "$PEPE safe?" classifies as a scan tool intent; stub the live tool
        # fetch so this test never reaches a real scan service (hermetic).
        patch.object(chat_mod, "_fetch_tool_context", new=AsyncMock(return_value=None)),
    ):
        resp = client.post("/api/v1/chat", json={"message": "is $PEPE safe?"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert resp.json()["message"] == "Fallback answer."


def test_client_context_override_skips_server_rag(client: TestClient) -> None:
    """Callers (Telegram bot) that already ground via context_override must
    not get a second server-side RAG search (no double-grounding)."""
    captured: dict[str, Any] = {}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured["messages"] = kwargs["messages"]
        return _completion_result("Bot-grounded answer.")

    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock()) as rag_mock,
    ):
        resp = client.post(
            "/api/v1/chat",
            json={"message": "hello", "context_override": {"system_context": "bot-provided RAG context"}},
        )

    assert resp.status_code == 200
    rag_mock.assert_not_awaited()
    assert resp.headers.get("X-RAG-Context") == "0"
    system_msg = captured["messages"][0]["content"]
    assert "bot-provided RAG context" in system_msg


# ── Response cache with caller-supplied grounding (Fix 2) ───────────────
# The cache key now folds in a fingerprint of context_override.system_context
# (the bot's persona), so a grounded request with the SAME grounding cache-hits
# (was: client_grounded bypassed the cache unconditionally — every bot turn
# paid a full 56-88s LLM call instead of an 11ms cache hit) while a DIFFERENT
# persona never serves a stale cached answer.


def test_grounded_repeat_request_hits_response_cache(client: TestClient) -> None:
    persona = "PERSONA — same grounding for both turns"
    calls = {"n": 0}

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _completion_result("Grounded answer.")

    with patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)):
        # First turn: cache miss → LLM call.
        r1 = client.post(
            "/api/v1/chat",
            json={"message": "what is a honeypot", "context_override": {"system_context": persona}},
        )
        assert r1.status_code == 200
        assert r1.json()["provider"] != "cache"
        assert calls["n"] == 1
        # Second turn: SAME message + SAME persona → cache hit, no LLM call.
        r2 = client.post(
            "/api/v1/chat",
            json={"message": "what is a honeypot", "context_override": {"system_context": persona}},
        )
        assert r2.status_code == 200
        assert r2.json()["provider"] == "cache"
        assert r2.json()["latency_ms"] == 0
        assert calls["n"] == 1


def test_grounded_request_different_persona_misses_cache(client: TestClient) -> None:
    """A DIFFERENT persona must NOT serve the cached answer — the cache is
    bypassed only when the grounding actually changes, not unconditionally."""

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("Answer.")

    with patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)):
        r1 = client.post(
            "/api/v1/chat",
            json={"message": "is x a rug pull", "context_override": {"system_context": "persona A"}},
        )
        assert r1.json()["provider"] != "cache"
        # Different grounding → key differs → miss (fresh LLM call).
        r2 = client.post(
            "/api/v1/chat",
            json={"message": "is x a rug pull", "context_override": {"system_context": "persona B"}},
        )
        assert r2.status_code == 200
        assert r2.json()["provider"] != "cache"
        # Same grounding as the first turn again → cache hit.
        r3 = client.post(
            "/api/v1/chat",
            json={"message": "is x a rug pull", "context_override": {"system_context": "persona A"}},
        )
        assert r3.json()["provider"] == "cache"


# ── POST /api/v1/chat/stream ──────────────────────────────────────────


def test_stream_sets_rag_context_header(client: TestClient) -> None:
    async def _fake_rag(message: str) -> tuple[str, int]:
        return _format_rag_context(
            [{"content": "stream hit", "score": 0.7, "collection": "news_v2", "doc_id": "d1"}]
        ), 1

    async def _fake_stream(messages: Any, **kwargs: Any) -> Any:
        assert messages[0]["role"] == "system"
        assert "RAG Context:" in messages[0]["content"]
        yield "streamed token"

    with (
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_fake_rag)),
        patch.object(chat_mod, "do_stream_chat_completion", side_effect=_fake_stream),
    ):
        resp = client.post("/api/v1/chat/stream", json={"message": "any stream message"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "1"
    body = resp.text
    assert "streamed token" in body
    assert '"type": "done"' in body


def test_stream_degrades_gracefully_when_rag_raises(client: TestClient) -> None:
    """The stream handler must survive a RAG search failure (own try/except)."""

    async def _fake_stream(messages: Any, **kwargs: Any) -> Any:
        yield "streamed despite rag down"

    async def _rag_down(message: str) -> tuple[str, int]:
        raise RuntimeError("RAG service unavailable")

    with (
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_rag_down)),
        patch.object(chat_mod, "do_stream_chat_completion", side_effect=_fake_stream),
    ):
        resp = client.post("/api/v1/chat/stream", json={"message": "is this token safe?"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "0"
    assert "streamed despite rag down" in resp.text
    assert '"type": "done"' in resp.text


def test_chat_with_token_address_queries_scanner_v2_collection(client: TestClient) -> None:
    """A message mentioning a token address must search scanner_v2 too, and
    the X-RAG-Context header must reflect the returned hit count."""

    async def _fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _completion_result("Grounded answer.")

    async def _fake_rag(message: str) -> tuple[str, int]:
        return _format_rag_context(
            [
                {
                    "content": "scanner_v2 result for this token",
                    "score": 0.95,
                    "collection": "scanner_v2",
                    "doc_id": "scan-1",
                }
            ]
        ), 1

    addr = "0x" + "c" * 40
    with (
        patch.object(chat_mod, "do_chat_completion", new=AsyncMock(side_effect=_fake_completion)),
        patch.object(chat_mod, "_search_rag_context", new=AsyncMock(side_effect=_fake_rag)),
        # A token-address message classifies as a "scan" tool intent, so
        # _gather_grounding would otherwise run a LIVE scan tool via
        # _fetch_tool_context. When that tool returns data it suppresses the
        # RAG context (double-grounding) and X-RAG-Context becomes "0" — an
        # order/network-dependent failure. Stub the tool fetch to miss so the
        # RAG path (the thing this test asserts) is deterministic.
        patch.object(chat_mod, "_fetch_tool_context", new=AsyncMock(return_value=None)),
    ):
        resp = client.post("/api/v1/chat", json={"message": f"is {addr} a honeypot?"})

    assert resp.status_code == 200
    assert resp.headers.get("X-RAG-Context") == "1"
    assert resp.json()["message"] == "Grounded answer."
