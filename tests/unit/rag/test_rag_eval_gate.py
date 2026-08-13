"""Unit tests for the G3 RAG regression gate (app.rag.evaluation).

Covers:
  1. Golden set is expanded (>= 20 QA pairs with question + ground_truth).
  2. ``_keyword_hit_rate`` deterministic hit@k proxy.
  3. ``thresholds_met`` fails below the hit_rate/faithfulness floors.
  4. ``evaluate_rag_pipeline`` (with a mocked search) returns hit_rate +
     faithfulness aggregates and a correct ``thresholds_met`` gate.
"""

from __future__ import annotations

import pytest

from app.rag.evaluation import (
    EVAL_FAITHFULNESS_MIN,
    EVAL_HIT_RATE_MIN,
    RUGBRAIN_GOLDEN_SET,
    _keyword_hit_rate,
    evaluate_rag_pipeline,
    thresholds_met,
)

# ── golden set ────────────────────────────────────────────────────────


def test_golden_set_expanded() -> None:
    assert len(RUGBRAIN_GOLDEN_SET) >= 20, f"golden set must be >= 20, got {len(RUGBRAIN_GOLDEN_SET)}"
    for entry in RUGBRAIN_GOLDEN_SET:
        assert entry.get("question"), f"missing question: {entry}"
        assert entry.get("ground_truth"), f"missing ground_truth: {entry}"


def test_golden_set_questions_unique() -> None:
    questions = [e["question"] for e in RUGBRAIN_GOLDEN_SET]
    assert len(set(questions)) == len(questions), "duplicate questions in golden set"


# ── hit-rate proxy ────────────────────────────────────────────────────


def test_keyword_hit_rate_positive() -> None:
    gt = "honeypot token blocks sells and charges high tax"
    chunks = ["A honeypot token blocks sells and charges a very high tax on every transfer."]
    assert _keyword_hit_rate(gt, chunks) == 1.0


def test_keyword_hit_rate_miss() -> None:
    gt = "reentrancy attacks exploit external calls before state updates"
    chunks = ["Bitcoin ETF inflows surge as institutions buy more."]
    assert _keyword_hit_rate(gt, chunks) == 0.0


def test_keyword_hit_rate_empty() -> None:
    assert _keyword_hit_rate("", ["a b c"]) == 0.0
    assert _keyword_hit_rate("question", []) == 0.0


# ── thresholds ────────────────────────────────────────────────────────


def test_thresholds_met_passes_when_above_floors() -> None:
    ok, failures = thresholds_met({"hit_rate": 0.9, "faithfulness": 0.6})
    assert ok is True
    assert failures == []


def test_thresholds_met_fails_on_low_hit_rate() -> None:
    ok, failures = thresholds_met({"hit_rate": 0.1, "faithfulness": 0.6})
    assert ok is False
    assert any("hit_rate" in f for f in failures)


def test_thresholds_met_fails_on_low_faithfulness() -> None:
    ok, failures = thresholds_met({"hit_rate": 0.9, "faithfulness": 0.05})
    assert ok is False
    assert any("faithfulness" in f for f in failures)


def test_thresholds_met_missing_metrics_fails() -> None:
    ok, failures = thresholds_met({})
    assert ok is False
    assert len(failures) == 2


def test_threshold_constants_sane() -> None:
    assert 0.0 < EVAL_HIT_RATE_MIN <= 1.0
    assert 0.0 < EVAL_FAITHFULNESS_MIN <= 1.0


# ── pipeline wiring (mocked search) ───────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_pipeline_computes_hit_rate_and_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run whose retrieved chunks always support the ground truth must
    produce hit_rate=1.0, faithfulness>0 and thresholds_met=True."""
    test_set = [
        {
            "question": "What is a honeypot token?",
            "ground_truth": "A honeypot token allows buys but blocks sells entirely.",
        },
        {
            "question": "What is reentrancy?",
            "ground_truth": "Reentrancy exploits external calls before state updates.",
        },
    ]

    async def fake_search_one(question: str, collection: str) -> dict:
        # Return a chunk that contains the ground-truth keywords.
        gt = next(tc["ground_truth"] for tc in test_set if tc["question"] == question)
        return {
            "context_chunks": [f"Relevant context: {gt} More detail here."],
            "doc_ids": ["doc:1"],
            "latency_ms": 5.0,
            "total": 1,
        }

    # Force the deterministic heuristic path (no ragas LLM judge): the unit
    # gate must be hermetic and fast even when ragas is importable.
    monkeypatch.setattr("app.rag.evaluation._ragas_metrics_available", lambda: False)
    monkeypatch.setattr("app.rag.evaluation._search_one", fake_search_one)

    result = await evaluate_rag_pipeline(test_set=test_set, collection="threat_intel_v2")

    assert result["metrics"]["hit_rate"] == 1.0
    assert result["metrics"]["faithfulness"] > 0.0
    assert result["metric_source"] == "heuristics"
    assert result["thresholds_met"] is True
    assert result["threshold_failures"] == []
    assert result["thresholds"]["hit_rate_min"] == EVAL_HIT_RATE_MIN
    assert result["thresholds"]["faithfulness_min"] == EVAL_FAITHFULNESS_MIN
    assert len(result["per_query"]) == 2


@pytest.mark.asyncio
async def test_evaluate_pipeline_gate_fails_on_bad_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """When retrieval returns irrelevant chunks the gate must fail."""
    test_set = [
        {
            "question": "What is a honeypot token?",
            "ground_truth": "A honeypot token allows buys but blocks sells entirely.",
        }
    ]

    async def fake_search_one(question: str, collection: str) -> dict:
        return {
            "context_chunks": ["Bitcoin ETF inflows surge as institutions accumulate."],
            "doc_ids": ["doc:1"],
            "latency_ms": 5.0,
            "total": 1,
        }

    monkeypatch.setattr("app.rag.evaluation._ragas_metrics_available", lambda: False)
    monkeypatch.setattr("app.rag.evaluation._search_one", fake_search_one)

    result = await evaluate_rag_pipeline(test_set=test_set, collection="threat_intel_v2")

    assert result["metrics"]["hit_rate"] == 0.0
    assert result["metrics"]["faithfulness"] == 0.0
    assert result["metric_source"] == "heuristics"
    assert result["thresholds_met"] is False
    assert len(result["threshold_failures"]) >= 1
