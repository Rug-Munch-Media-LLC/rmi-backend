"""Unit tests for RAG query expansion and hybrid-retrieval rerank dedup.

Covers:
  1. ``QueryExpander.expand`` — single-term synonyms for the expanded
     domains (TA, DeFi, security, tokenomics, market microstructure).
  2. ``QueryExpander.expand`` — bigram phrase expansion (e.g. "order block").
  3. ``QueryExpander.expand`` — original query preserved, term cap, no-op
     for empty/None.
  4. ``HybridRetriever.retrieve`` — skips the duplicate cross-encoder pass
     when the engine's mandatory rerank already stamped ``rerank_score``
     (one Infinity call per query, not two).
  5. ``HybridRetriever.retrieve`` — still reranks when the engine could not
     (no ``rerank_score`` present).
"""

from __future__ import annotations

from unittest import mock

from app.rag.retrieval import HybridRetriever, QueryExpander

# ── QueryExpander ──────────────────────────────────────────────────────


def test_expander_adds_domain_synonyms() -> None:
    exp = QueryExpander()
    # Security-domain recall: reentrancy synonyms lead the cap (single-term query).
    low = exp.expand("reentrancy").lower()
    assert "reentrant" in low or "external_call" in low
    # DeFi-domain recall: MEV synonyms.
    low = exp.expand("mev").lower()
    assert "sandwich" in low or "front_running" in low or "maximal_extractable_value" in low
    # Original query kept verbatim (prefix).
    expanded = exp.expand("is this token vulnerable to reentrancy", max_terms=6).lower()
    assert expanded.startswith("is this token vulnerable to reentrancy")


def test_expander_ta_and_tokenomics_terms() -> None:
    exp = QueryExpander()
    low = exp.expand("support resistance levels").lower()
    assert "demand_zone" in low or "supply_zone" in low or "liquidity_zone" in low
    low = exp.expand("check vesting and emissions schedule").lower()
    assert "cliff" in low or "unlock_schedule" in low or "inflation" in low


def test_expander_bigram_phrase_expansion() -> None:
    exp = QueryExpander()
    expanded = exp.expand("order block analysis").lower()
    # Bigram "order_block" fires the phrase map.
    assert "supply" in expanded
    assert "demand" in expanded
    assert "zone" in expanded


def test_expander_caps_extra_terms() -> None:
    exp = QueryExpander()
    # "liquidity" alone maps to many synonyms; cap at RAG_EXPANSION_TERMS=3.
    expanded = exp.expand("liquidity yield farm", max_terms=3)
    tokens = expanded.split()
    original_len = 3
    assert len(tokens) <= original_len + 3


def test_expander_empty_and_unknown_query() -> None:
    exp = QueryExpander()
    assert exp.expand("") == ""
    assert exp.expand("   ") == "   "
    # Unknown terms: expansion is a no-op, original preserved.
    assert exp.expand("the sky is blue today") == "the sky is blue today"


def test_expander_variants() -> None:
    exp = QueryExpander()
    low = exp.expand("what is defi").lower()
    assert "decentralized_finance" in low


# ── HybridRetriever double-rerank dedup ────────────────────────────────


def _engine_hits(ranks: list[float]) -> list[dict]:
    """Engine-shaped hits; ``ranks`` are the rerank relevance scores."""
    return [
        {
            "doc_id": f"doc-{i}",
            "score": s,
            "text": f"document about crypto number {i}",
            "metadata": {"rerank_score": round(s, 4)},
        }
        for i, s in enumerate(ranks)
    ]


async def test_retrieve_skips_second_rerank_when_engine_reranked() -> None:
    retriever = HybridRetriever()
    engine_hits = _engine_hits([0.9, 0.7, 0.3])
    with (
        mock.patch("app.rag.engine.three_pillar_search", return_value=engine_hits),
        mock.patch("app.rag.retrieval.rerank_results", new_callable=mock.AsyncMock) as rerank,
    ):
        hits = await retriever.retrieve("reentrancy flash loan", top_k=3, rerank=True)
    # Engine rerank scores present → the retriever must NOT call Infinity again.
    rerank.assert_not_awaited()
    assert [h.doc_id for h in hits] == ["doc-0", "doc-1", "doc-2"]  # engine order preserved


async def test_retrieve_still_reranks_when_engine_did_not() -> None:
    retriever = HybridRetriever()
    # Engine path failed to rerank (no rerank_score) → retriever fallback fires.
    engine_hits = [
        {"doc_id": "doc-0", "score": 0.5, "text": "first doc", "metadata": {}},
        {"doc_id": "doc-1", "score": 0.4, "text": "second doc", "metadata": {}},
    ]
    reranked = [
        {"index": 1, "relevance_score": 0.95},
        {"index": 0, "relevance_score": 0.10},
    ]
    with (
        mock.patch("app.rag.engine.three_pillar_search", return_value=engine_hits),
        mock.patch("app.rag.retrieval.rerank_results", new_callable=mock.AsyncMock) as rerank,
    ):
        rerank.return_value = reranked
        hits = await retriever.retrieve("some query", top_k=2, rerank=True)
    rerank.assert_awaited_once()
    assert [h.doc_id for h in hits] == ["doc-1", "doc-0"]  # rerank order applied
    assert all(h.final_score > 0.0 for h in hits)
