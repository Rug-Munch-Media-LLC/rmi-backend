"""RAG retrieval quality tests — hybrid search, rerank, query expansion,
context building, news boosts, entity extraction.

All external dependencies (Qdrant via the M3 engine's ``three_pillar_search``,
the knowledge graph, the Qwen3 reranker) are mocked with ``unittest.mock`` so
these tests exercise pure ranking/assembly logic deterministically.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.retrieval.filters import apply_news_boosts, extract_crypto_entities
from app.rag.retrieval.helpers import ContextBuilder, ScoredHit
from app.rag.retrieval.rerank import RelevanceScorer
from app.rag.retrieval.search import HybridRetriever, QueryExpander


@pytest.fixture
def mock_engine(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub the ``app.rag.engine`` module (Qdrant-backed M3 engine).

    ``HybridRetriever.retrieve`` does ``from app.rag.engine import
    three_pillar_search`` at call time; installing a stub module in
    ``sys.modules`` lets us mock the Qdrant-backed search deterministically.
    """
    mod = types.ModuleType("app.rag.engine")
    mod.three_pillar_search = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.rag.engine", mod)
    return mod.three_pillar_search  # type: ignore[no-any-return]


def _doc(i: int, score: float, content: str = "") -> dict:
    """Build a raw engine hit dict (the shape three_pillar_search returns)."""
    return {
        "id": f"doc-{i}",
        "doc_id": f"doc-{i}",
        "content": content or f"document {i} about crypto security",
        "score": score,
        "metadata": {},
        "collection": "scanner",
    }


# ── Hybrid search ────────────────────────────────────────────────────


def test_hybrid_search_returns_relevant_results(mock_engine: AsyncMock) -> None:
    """Mock Qdrant (via three_pillar_search) returning 5 docs → search
    returns all 5, ranked by relevance (final_score descending)."""
    docs = [_doc(i, score=0.9 - i * 0.1) for i in range(5)]  # 0.9 … 0.5
    mock_engine.return_value = docs

    retriever = HybridRetriever()
    with patch(
        "app.knowledge_graph.graph.KnowledgeGraph",
        new=MagicMock(return_value=MagicMock(extract_entities=AsyncMock(return_value=[]))),
    ):
        hits = asyncio.run(retriever.retrieve("crypto security", collection="scanner", top_k=5))

    assert len(hits) == 5
    scores = [h.final_score for h in hits]
    assert scores == sorted(scores, reverse=True), "results must be ranked by relevance"
    assert {h.doc_id for h in hits} == {f"doc-{i}" for i in range(5)}


def test_rerank_changes_order() -> None:
    """RelevanceScorer must reorder hits when keyword overlap outweighs the
    raw base score: a lower-scored doc that matches the query terms should
    overtake a higher-scored doc with zero overlap."""
    query = "bitcoin halving supply"
    hits = [
        {"id": "a", "content": "totally unrelated text here", "score": 0.80},
        {"id": "b", "content": "bitcoin halving supply schedule", "score": 0.60},
    ]

    scored = RelevanceScorer().rescore(hits, query)

    assert [s.doc_id for s in scored] == ["b", "a"], "overlap-rich doc must overtake"
    assert scored[0].final_score > scored[1].final_score


# ── Query expansion ──────────────────────────────────────────────────


def test_query_expansion_adds_synonyms() -> None:
    """Expansion appends synonyms for known terms while preserving the
    original query verbatim (recall lever, never sacrifices precision).

    Ticker→canonical-name mapping ("BTC" → "bitcoin") lives in the entity
    canon (filters.py), not the synonym map — verified here too.
    """
    expander = QueryExpander()

    expanded = expander.expand("rug pull liquidity")
    assert expanded.startswith("rug pull liquidity"), "original query kept verbatim"
    assert "removal" in expanded  # bigram phrase expansion: rug_pull → …
    assert "dump" in expanded

    # Ticker canonicalisation: "BTC" → "bitcoin" via the entity canon.
    assert extract_crypto_entities("BTC") == ["bitcoin"]
    assert extract_crypto_entities("BTC Bitcoin") == ["bitcoin"]


# ── Context building ─────────────────────────────────────────────────


def test_context_builder_assembles_context() -> None:
    """ContextBuilder assembles hits into a formatted window with sources,
    score-ranked ordering, and a token estimate."""
    hits = [
        ScoredHit(content="first doc", score=0.9, collection="scanner", doc_id="d1"),
        ScoredHit(content="second doc", score=0.5, collection="news", doc_id="d2"),
    ]

    ctx = ContextBuilder().build(hits)

    assert "first doc" in ctx.formatted
    assert "second doc" in ctx.formatted
    assert ctx.formatted.index("first doc") < ctx.formatted.index("second doc"), "higher-scored doc appears first"
    assert set(ctx.sources) == {"scanner", "news"}
    assert ctx.total_tokens_est > 0
    assert len(ctx.hits) == 2


# ── Fail-open behaviour ──────────────────────────────────────────────


def test_empty_results_failopen(mock_engine: AsyncMock) -> None:
    """Empty Qdrant results must not crash: retrieval returns an empty list
    and the scorer is a no-op."""
    mock_engine.return_value = []
    retriever = HybridRetriever()
    with patch(
        "app.knowledge_graph.graph.KnowledgeGraph",
        new=MagicMock(return_value=MagicMock(extract_entities=AsyncMock(return_value=[]))),
    ):
        hits = asyncio.run(retriever.retrieve("anything", collection="scanner", top_k=5))

    assert hits == []
    assert RelevanceScorer().rescore([], "query") == []


# ── News boosts ──────────────────────────────────────────────────────


def test_news_boost_applies_to_recent() -> None:
    """A news article published <1h ago gets the breaking-news freshness
    boost (x1.5) applied to score and final_score."""
    now = datetime.now(UTC)
    hit = ScoredHit(
        content="bitcoin etf approved",
        score=1.0,
        final_score=1.0,
        collection="news",
        doc_id="n1",
        metadata={"published_at": (now - timedelta(minutes=30)).isoformat()},
    )

    boosted = apply_news_boosts([hit], query="regulation", collection="news", now=now)

    assert boosted[0].final_score == 1.5
    assert boosted[0].score == 1.5
    assert boosted[0].metadata["news_boost"] == 1.5


# ── Entity extraction ────────────────────────────────────────────────


def test_entity_extraction_finds_crypto_terms() -> None:
    """Crypto entities are extracted as canonical lowercased labels from
    free text (tickers and names alike)."""
    assert extract_crypto_entities("ethereum and solana rally") == ["ethereum", "solana"]
    assert extract_crypto_entities("ETH SOL uniswap") == ["ethereum", "solana", "uniswap"]
    assert extract_crypto_entities("no entities here") == []
    assert extract_crypto_entities("") == []
