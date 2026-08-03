"""Unit tests for the LangGraph agentic RAG orchestration (app.rag.graph).

Covers:
  1. Graph compiles with the expected nodes/edges.
  2. classify_query falls back to heuristic classification when the LLM is down.
  3. route_query maps every query type to a valid branch (general → news).
  4. Each retrieve_* node calls HybridRetriever with the right collection.
  5. rerank_documents reorders via the Infinity cross-encoder (fail-open).
  6. synthesize_answer uses tools + template fallback when LLM is down.
  7. verify_sources marks answers without citations as unverified.
  8. End-to-end ainvoke produces an answer + sources.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.graph import (
    TOOLS,
    classify_query,
    create_crypto_rag_graph,
    rerank_documents,
    retrieve_security,
    route_query,
    synthesize_answer,
    verify_sources,
)
from app.rag.retrieval import ScoredHit


def _state(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "query": "is this token a honeypot?",
        "query_type": "general",
        "documents": [],
        "context": "",
        "answer": "",
        "sources": [],
        "tool_calls": [],
    }
    defaults.update(overrides)
    return defaults


def _hit(content: str = "Past honeypot pattern", score: float = 0.8, collection: str = "scams") -> ScoredHit:
    return ScoredHit(
        content=content,
        score=score,
        final_score=score,
        collection=collection,
        doc_id=f"{collection}:1",
    )


# ── Graph assembly ────────────────────────────────────────────────────


def test_graph_compiles_with_expected_nodes() -> None:
    graph = create_crypto_rag_graph()
    nodes = getattr(graph, "nodes", {})
    assert "classify" in nodes
    assert "retrieve_security" in nodes
    assert "retrieve_market" in nodes
    assert "retrieve_code" in nodes
    assert "retrieve_news" in nodes
    assert "rerank" in nodes
    assert "synthesize" in nodes
    assert "verify" in nodes


def test_tools_registry_exposes_all_real_time_tools() -> None:
    assert set(TOOLS) == {
        "get_token_price",
        "get_gas_price",
        "get_wallet_balance",
        "scan_token",
        "get_news",
    }


# ── classify ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_heuristic_fallback_when_llm_down() -> None:
    with patch("app.rag.graph._classify_with_llm", side_effect=ConnectionError("llm down")):
        result = await classify_query(_state(query="is this token a honeypot rugpull?"))
    assert result["query_type"] == "security"


@pytest.mark.asyncio
async def test_classify_heuristic_defaults_to_general() -> None:
    result = await classify_query(_state(query="what's the meaning of life in defi?"))
    assert result["query_type"] in {"general", "news", "market"}


# ── route ─────────────────────────────────────────────────────────────


def test_route_query_maps_valid_types() -> None:
    assert route_query(_state(query_type="security")) == "security"
    assert route_query(_state(query_type="market")) == "market"
    assert route_query(_state(query_type="code")) == "code"
    assert route_query(_state(query_type="news")) == "news"


def test_route_query_general_falls_back_to_news() -> None:
    assert route_query(_state(query_type="general")) == "news"
    assert route_query(_state(query_type="bogus")) == "news"


# ── retrieval ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieve_security_uses_scams_collection() -> None:
    retriever = AsyncMock()
    retriever.retrieve.return_value = [_hit()]
    with patch("app.rag.graph.HybridRetriever", return_value=retriever):
        result = await retrieve_security(_state())
    assert result["documents"]
    assert result["documents"][0]["collection"] == "scams"
    retriever.retrieve.assert_awaited_once()
    kwargs = retriever.retrieve.await_args.kwargs
    assert kwargs["collection"] == "scams"


@pytest.mark.asyncio
async def test_retrieve_fails_open_to_empty_documents() -> None:
    retriever = AsyncMock()
    retriever.retrieve.side_effect = ConnectionError("rag down")
    with patch("app.rag.graph.HybridRetriever", return_value=retriever):
        result = await retrieve_security(_state())
    assert result["documents"] == []


# ── rerank ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_reorders_documents() -> None:
    docs = [
        {"content": "low relevance", "collection": "scams", "score": 0.1},
        {"content": "high relevance", "collection": "scams", "score": 0.9},
    ]
    reranked = [{"index": 1, "relevance_score": 0.95}, {"index": 0, "relevance_score": 0.2}]
    with patch("app.rag.graph.rerank_results", new=AsyncMock(return_value=reranked)):
        result = await rerank_documents(_state(documents=docs, query="honeypot?"))
    assert result["documents"][0]["content"] == "high relevance"


@pytest.mark.asyncio
async def test_rerank_fails_open_keeps_order() -> None:
    docs = [{"content": "a", "collection": "scams"}, {"content": "b", "collection": "scams"}]
    with patch("app.rag.graph.rerank_results", new=AsyncMock(side_effect=ConnectionError("rerank down"))):
        result = await rerank_documents(_state(documents=docs, query="honeypot?"))
    assert [d["content"] for d in result["documents"]] == ["a", "b"]


# ── synthesize ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_uses_llm_and_records_tool_calls() -> None:
    docs = [{"content": "Known honeypot on this deployer", "collection": "scams", "score": 0.9}]
    with (
        patch("app.rag.graph._synthesize_with_llm", new=AsyncMock(return_value="This looks like a honeypot.")),
        patch(
            "app.rag.graph._call_tools",
            new=AsyncMock(return_value=[{"tool": "scan_token", "result": {"risk": "high"}}]),
        ),
    ):
        result = await synthesize_answer(_state(query_type="security", documents=docs, query="is this a honeypot?"))
    assert result["answer"] == "This looks like a honeypot."
    assert result["tool_calls"] == [{"tool": "scan_token", "result": {"risk": "high"}}]
    assert result["sources"] == ["scams"]


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_template_when_llm_down() -> None:
    docs = [{"content": "Known honeypot pattern", "collection": "scams", "score": 0.9}]
    with (
        patch("app.rag.graph._synthesize_with_llm", side_effect=ConnectionError("llm down")),
        patch("app.rag.graph._call_tools", new=AsyncMock(return_value=[])),
    ):
        result = await synthesize_answer(_state(query_type="security", documents=docs, query="honeypot?"))
    assert "Retrieved documents" in result["answer"]
    assert "Confidence: low" in result["answer"]


@pytest.mark.asyncio
async def test_call_tools_never_breaks_synthesis() -> None:
    docs: list[dict[str, Any]] = []
    with (
        patch("app.rag.graph._synthesize_with_llm", new=AsyncMock(return_value="ok")),
        patch("app.rag.graph.TOOLS", {"scan_token": AsyncMock(side_effect=RuntimeError("boom"))}),
    ):
        result = await synthesize_answer(_state(query_type="security", documents=docs, query="honeypot?"))
    assert result["answer"] == "ok"
    assert result["tool_calls"][0]["error"] == "boom"


# ── verify ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_marks_uncited_answer_as_unverified() -> None:
    result = await verify_sources(_state(answer="SOL will moon.", sources=[]))
    assert result["sources"] == ["__unverified__"]


@pytest.mark.asyncio
async def test_verify_keeps_cited_sources_deduped() -> None:
    result = await verify_sources(_state(answer="SOL is up.", sources=["news", "news", "scams"]))
    assert result["sources"] == ["news", "scams"]


# ── end-to-end ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ainvoke_full_flow_market_query() -> None:
    graph = create_crypto_rag_graph()
    retriever = AsyncMock()
    retriever.retrieve.return_value = [_hit(content="SOL up on strong volume", score=0.9, collection="news")]

    with (
        patch("app.rag.graph._classify_with_llm", new=AsyncMock(return_value="market")),
        patch("app.rag.graph.HybridRetriever", return_value=retriever),
        patch("app.rag.graph.rerank_results", new=AsyncMock(return_value=[{"index": 0, "relevance_score": 0.9}])),
        patch(
            "app.rag.graph._synthesize_with_llm",
            new=AsyncMock(return_value="SOL is up on strong volume.\nSources: news\nConfidence: high"),
        ),
        patch(
            "app.rag.graph._call_tools",
            new=AsyncMock(return_value=[{"tool": "get_token_price", "result": {"price_usd": 150.0}}]),
        ),
    ):
        result = await graph.ainvoke({"query": "what is the SOL price trend?"})

    assert result["query_type"] == "market"
    assert "SOL" in result["answer"]
    assert result["sources"] == ["news"]
    assert result["tool_calls"][0]["tool"] == "get_token_price"
