"""Unit tests for the G2 mandatory-rerank + context-budget gate.

Covers ``app.rag.engine._apply_context_budget`` (hard token budget applied
after rerank) and ``app.rag.engine._rerank_hits`` (mandatory cross-encoder
rerank, fail-open, reorders by relevance and stamps ``rerank_score``).
"""

from __future__ import annotations

import pytest

from app.rag.ann_index import Hit
from app.rag.engine import _apply_context_budget, _rerank_hits


def _hit(doc_id: str, text: str, score: float = 0.5) -> Hit:
    return Hit(doc_id=doc_id, score=score, text=text, metadata={})


# ── _apply_context_budget ─────────────────────────────────────────────


def test_budget_keeps_hits_within_token_limit() -> None:
    hits = [
        _hit("a", "one two three four five six seven eight nine ten", 0.9),
        _hit("b", "alpha beta gamma delta epsilon zeta eta theta iota kappa", 0.8),
        _hit("c", "foo bar baz qux quux corge grault garply waldo fred", 0.7),
    ]
    # Each hit is 10 words → ≥10 tokens (word heuristic). Budget 25 tokens
    # must stop before the third hit.
    out = _apply_context_budget(hits, max_tokens=25)
    assert len(out) == 2
    assert [h.doc_id for h in out] == ["a", "b"]


def test_budget_always_keeps_at_least_one_hit() -> None:
    hits = [_hit("big", "word " * 500, 0.9)]
    out = _apply_context_budget(hits, max_tokens=10)
    assert [h.doc_id for h in out] == ["big"]


def test_budget_zero_or_negative_is_noop() -> None:
    hits = [_hit("a", "hello"), _hit("b", "world")]
    assert _apply_context_budget(hits, max_tokens=0) == hits
    assert _apply_context_budget(hits, max_tokens=-5) == hits


def test_budget_empty_hits() -> None:
    assert _apply_context_budget([], max_tokens=4096) == []


# ── _rerank_hits ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_reorders_by_relevance_and_stamps_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a mock cross-encoder the reranker is mandatory and reorders."""
    hits = [_hit("low", "irrelevant text"), _hit("high", "highly relevant text")]

    async def fake_rerank(query: str, documents: list[str], top_n: int) -> list[dict]:
        # Reverse order: index 1 is most relevant.
        return [
            {"index": 1, "relevance_score": 0.92},
            {"index": 0, "relevance_score": 0.31},
        ]

    monkeypatch.setattr("app.rag.rerank.rerank_results", fake_rerank)
    out = await _rerank_hits("query", hits)

    assert [h.doc_id for h in out] == ["high", "low"]
    assert out[0].score == 0.92
    assert out[0].metadata["rerank_score"] == 0.92
    assert out[1].metadata["rerank_score"] == 0.31


@pytest.mark.asyncio
async def test_rerank_fail_open_keeps_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reranker outage must not break search — pre-rerank order is kept."""
    hits = [_hit("a", "first"), _hit("b", "second")]

    async def fake_rerank(query: str, documents: list[str], top_n: int) -> list[dict]:
        return []

    monkeypatch.setattr("app.rag.rerank.rerank_results", fake_rerank)
    out = await _rerank_hits("query", hits)

    assert [h.doc_id for h in out] == ["a", "b"]
    assert "rerank_score" not in out[0].metadata


@pytest.mark.asyncio
async def test_rerank_single_hit_skipped() -> None:
    out = await _rerank_hits("query", [_hit("only", "sole result")])
    assert [h.doc_id for h in out] == ["only"]
