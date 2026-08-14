"""Unit tests for the rag_lookup tool (tools/rag_lookup.py).

Contract under test:
  should_rag_lookup    — knowledge-question gate (definitions, history,
                         methodology, patterns) vs live-market/scan asks
  _build_query         — message + explicit token hints
  _format_context      — labeled RAG context block for the LLM prompt
  rag_lookup           — parallel multi-collection search via the backend RAG
                         facade, deduped + scored, ok=False on failure

No network I/O: the backend RAG facade (backend_json_post) is mocked at the
module boundary.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domains.telegram.rugmunchbot.tools import rag_lookup as mod
from app.domains.telegram.rugmunchbot.tools.rag_lookup import (
    KNOWLEDGE_COLLECTIONS,
    RAG_LOOKUP_TOP_K,
    _build_query,
    _format_context,
    rag_lookup,
    should_rag_lookup,
)


def _run(coro: object) -> object:
    return asyncio.run(coro)


def _hit(content: str, score: float, collection: str = "scam_patterns") -> dict[str, Any]:
    return {"content": content, "score": score, "collection": collection, "doc_id": "d1"}


class TestShouldRagLookup:
    def test_knowledge_questions_pass(self) -> None:
        for q in (
            "what is a honeypot",
            "what are rugpull patterns",
            "how does a honeypot work",
            "explain the project history of rug munch",
            "what is the methodology behind wallet clustering",
            "why do rug pulls happen",
            "tell me about sandwich attacks",
        ):
            assert should_rag_lookup(q) is True, q

    def test_market_and_scan_asks_skip(self) -> None:
        for q in (
            "what is the price of bonk",
            "how much is btc worth",
            "scan 0x1234567890abcdef1234567890abcdef12345678",
            "btc price now",
            "is 0x1234567890abcdef1234567890abcdef12345678 a honeypot",
            "trending tokens today",
        ):
            assert should_rag_lookup(q) is False, q

    def test_empty_and_blank(self) -> None:
        assert should_rag_lookup("") is False
        assert should_rag_lookup("   ") is False


class TestBuildQuery:
    def test_plain_message(self) -> None:
        assert _build_query("what is a honeypot", [], []) == "what is a honeypot"

    def test_with_address_hint(self) -> None:
        q = _build_query("check this token", ["0xabc"], [])
        assert "0xabc" in q
        assert "token address" in q

    def test_with_symbol_hint(self) -> None:
        q = _build_query("what is this coin", [], ["PEPE"])
        assert "$PEPE" in q


class TestFormatContext:
    def test_hits_labeled_and_truncated(self) -> None:
        hits = [_hit("content A", 0.91), _hit("content B", 0.82)]
        ctx = _format_context(hits, max_chars=500)
        assert "[1] Source: scam_patterns (score: 0.910)" in ctx
        assert "[2] Source: scam_patterns (score: 0.820)" in ctx
        assert "content A" in ctx

    def test_empty_hits(self) -> None:
        assert _format_context([], max_chars=500) == ""


class TestRagLookup:
    @pytest.fixture(autouse=True)
    def _fake_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._calls: list[tuple[str, dict[str, Any]]] = []

        async def _fake_post(
            path: str, *, json: dict[str, Any] | None = None, timeout: float = 8.0
        ) -> dict[str, Any] | None:
            self._calls.append((path, json or {}))
            collection = (json or {}).get("collection", "")
            query = str((json or {}).get("query", ""))
            if collection == "scam_patterns" and "honeypot" in query:
                return {"hits": [_hit("honeypot pattern doc", 0.9)]}
            return {"hits": []}

        monkeypatch.setattr(mod, "backend_json_post", AsyncMock(side_effect=_fake_post))

    def test_knowledge_query_searches_collections(self) -> None:
        result = _run(rag_lookup(query="what is a honeypot"))
        assert result.ok is True
        assert result.tool == "rag_lookup"
        assert "honeypot pattern doc" in result.data["context"]  # type: ignore[index]
        assert result.data["sources"] == [  # type: ignore[index]
            {"type": "rag", "query": "what is a honeypot", "collection": "scam_patterns"}
        ]
        assert len(self._calls) == len(KNOWLEDGE_COLLECTIONS)
        for path, payload in self._calls:
            assert path == "/api/v1/rag/v2/search"
            assert payload["top_k"] == RAG_LOOKUP_TOP_K
            assert payload["min_similarity"] == 0.0
        # scanner_v2 is NOT searched without an address in the question.
        assert all(p["collection"] != "scanner_v2" for _, p in self._calls)

    def test_non_knowledge_query_skipped(self) -> None:
        result = _run(rag_lookup(query="btc price now"))
        assert result.ok is False
        assert "not a knowledge question" in (result.error or "")
        assert self._calls == []

    def test_empty_query(self) -> None:
        result = _run(rag_lookup(query=""))
        assert result.ok is False

    def test_no_hits_ok_false(self) -> None:
        result = _run(rag_lookup(query="what is an obscure invented concept"))
        assert result.ok is False
        assert result.error == "no rag hits"
