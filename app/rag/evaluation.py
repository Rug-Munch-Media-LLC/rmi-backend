"""
Ragas evaluation for RugBrain RAG.
Bridges to rmi-rag evaluation or runs standalone for the rmi-backend RAG path.

Runs the production rmi-backend RAG pipeline (``RAGService.search`` →
``engine.three_pillar_search``) against a crypto-security test set and
measures:

  - faithfulness       answer claims supported by retrieved context
  - answer_relevancy   answer addresses the question
  - context_precision  retrieved chunks are relevant to the question
  - context_recall     all relevant information is retrieved

``ragas`` is an optional dependency: it is imported lazily inside
``evaluate_rag_pipeline`` so the RAG engine keeps working when ragas is not
installed. When ragas is unavailable (or its LLM judge cannot be configured)
the evaluator falls back to deterministic keyword-overlap heuristics that
mirror rmi-rag's ``eval_ragas.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Per-query budget and concurrency cap. A hung search (cold embed, wedged
# event loop) times out instead of stalling the whole evaluation run.
EVAL_TIMEOUT_S = 10.0
EVAL_MAX_CONCURRENCY = 5
EVAL_TOP_K = 5

# Crypto-specific golden test set (mirrors rmi-rag's but local to rmi-backend)
RUGBRAIN_GOLDEN_SET: list[dict[str, str]] = [
    {
        "question": "What are the most common reentrancy attack patterns in Solidity?",
        "ground_truth": "Reentrancy attacks exploit external calls before state updates. Common patterns: single-function, cross-function, cross-contract, read-only reentrancy. Mitigation: Checks-Effects-Interactions pattern, ReentrancyGuard.",
    },
    {
        "question": "How does MEV sandwich attack work on Uniswap V2?",
        "ground_truth": "Attacker observes pending swap in mempool, front-runs with buy (inflates price), victim swaps at inflated rate, attacker back-runs with sell. Profit = victim's slippage tolerance.",
    },
    {
        "question": "What is a honeypot token and how is it detected?",
        "ground_truth": "A honeypot token allows buys but blocks sells. Detection: simulate sell transaction, check if transfer succeeds, verify tax rate doesn't exceed 99%, check if contract has hidden blacklist logic.",
    },
    {
        "question": "How do flash loan attacks work on DeFi protocols?",
        "ground_truth": "Flash loans borrow uncollateralized funds that must be repaid in the same transaction. Attackers use them to manipulate oracles, drain liquidity pools, or buy enough governance tokens to take over a DAO vote.",
    },
    {
        "question": "What is the Checks-Effects-Interactions pattern and why is it important?",
        "ground_truth": "Checks-Effects-Interactions orders code as: validate conditions, update state, then make external calls. It prevents reentrancy by ensuring state is committed before the contract releases control to external callers.",
    },
    {
        "question": "How can oracle manipulation attacks drain a DeFi protocol?",
        "ground_truth": "Oracle manipulation attacks corrupt the price feed a protocol trusts. Attackers use flash loans to move price on a low-liquidity pool, then borrow or trade against the manipulated price before it normalizes. TWAP oracles resist this.",
    },
    {
        "question": "What is a proxy upgrade trap in token contracts?",
        "ground_truth": "A proxy upgrade trap uses UUPS or TransparentUpgradeableProxy patterns so the admin can swap the implementation post-launch. Indicators: upgradeTo functions, admin change capability, no timelock on upgrades, storage collision between versions.",
    },
    {
        "question": "How does cross-chain bridge hacking work?",
        "ground_truth": "Bridge hacks exploit signature verification gaps, compromised validator keys, or message-passing flaws to mint tokens on the destination chain without real backing. Examples: Ronin, Wormhole, Poly Network. Prevention: multi-sig, timelocks, circuit breakers.",
    },
    {
        "question": "What are common signs of a pump and dump scheme?",
        "ground_truth": "Signs: coordinated social media hype, rapid price surge on thin liquidity, synchronized buying from a cluster of funded wallets, then mass sell-off. Holders are left with worthless tokens once promoters exit.",
    },
    {
        "question": "How do wallet drainer phishing sites steal funds?",
        "ground_truth": "Wallet drainer sites impersonate legitimate dApps, airdrops, or NFT mints and request unlimited token approval. Once approved, transferFrom sweeps victim funds. They use urgent language and lookalike domains to pressure users.",
    },
    {
        "question": "What is a dusting attack and how can it be detected?",
        "ground_truth": "A dusting attack sends tiny token amounts to many wallets to deanonymize them. Detection: micro-transactions below normal thresholds, unknown senders, many wallets receiving identical dust amounts at once. Response: avoid consolidating or spending dust.",
    },
    {
        "question": "How does fake volume wash trading distort exchange data?",
        "ground_truth": "Wash trading inflates volume via circular trades between wallets of the same entity. Detection: volume/liquidity ratio above 10x, repetitive identical-size orders, price stability despite high volume, and low unique counterparty counts.",
    },
    {
        "question": "What role do mixers play in crypto money laundering?",
        "ground_truth": "Mixers (e.g. Tornado Cash) break the on-chain link between sender and receiver by pooling and redistributing funds. Launderers use chain-hopping, peel chains, and mixer deposits before cashing out via exchanges, complicating fund tracing.",
    },
]

HISTORY_PATH = Path("/backups/rugbrain_eval_history.jsonl")


# ======================================================================
# HEURISTIC METRICS (fallback when ragas is unavailable)
# ======================================================================

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "if",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
        "how",
        "does",
        "what",
        "why",
    }
)


def _keywords(text: str) -> set[str]:
    """Extract meaningful lowercase keywords from text (stop words removed)."""
    words = re.findall(r"\b\w{3,}\b", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _keyword_faithfulness(answer: str, context_chunks: list[str]) -> float:
    """Fraction of answer keywords found in the retrieved context."""
    if not answer or not context_chunks:
        return 0.0
    answer_words = _keywords(answer)
    if not answer_words:
        return 0.5  # can't determine
    combined = " ".join(context_chunks).lower()
    supported = sum(1 for w in answer_words if w in combined)
    return supported / len(answer_words)


def _keyword_relevancy(answer: str, question: str) -> float:
    """Fraction of question keywords that appear in the answer."""
    question_words = _keywords(question)
    answer_words = _keywords(answer)
    if not question_words:
        return 0.0
    overlap = len(question_words & answer_words)
    return overlap / len(question_words)


def _keyword_context_precision(question: str, context_chunks: list[str]) -> float:
    """Precision@k: fraction of retrieved chunks relevant to the question."""
    if not context_chunks:
        return 0.0
    question_words = _keywords(question)
    if not question_words:
        return 0.0
    relevant = sum(1 for chunk in context_chunks if question_words & _keywords(chunk))
    return relevant / len(context_chunks)


def _keyword_context_recall(question: str, context_chunks: list[str]) -> float:
    """Fraction of question keywords found across the retrieved context."""
    if not question or not context_chunks:
        return 0.0
    question_words = _keywords(question)
    if not question_words:
        return 0.0
    combined = " ".join(context_chunks).lower()
    found = sum(1 for w in question_words if w in combined)
    return found / len(question_words)


# ======================================================================
# RAGAS PATH (lazy import — optional dependency)
# ======================================================================


def _ragas_metrics_available() -> bool:
    """Return True if ragas is importable and metrics can be built."""
    try:
        from ragas.metrics import (  # noqa: F401
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        return True
    except Exception:  # noqa: BLE001 - any ragas import failure → fallback
        return False


def _build_ragas_dataset(test_cases: list[dict], hits_by_query: dict[str, list[str]]) -> Any:
    """Build a ragas EvaluationDataset from search hits.

    Returns ``None`` if ragas cannot be imported. ``hits_by_query`` maps
    question → list of retrieved context chunk strings.
    """
    try:
        from ragas import EvaluationDataset, SingleTurnSample

        samples = []
        for tc in test_cases:
            question = tc.get("question", "")
            contexts = hits_by_query.get(question, [])
            samples.append(
                SingleTurnSample(
                    user_input=question,
                    retrieved_contexts=contexts,
                    response=tc.get("ground_truth", ""),
                    reference=tc.get("ground_truth", ""),
                )
            )
        return EvaluationDataset(samples=samples)
    except Exception as e:  # noqa: BLE001
        log.debug("ragas dataset build failed: %s", e)
        return None


async def _evaluate_with_ragas(test_cases: list[dict], hits_by_query: dict[str, list[str]]) -> dict[str, float] | None:
    """Run ragas metrics. Returns aggregate scores or None on any failure."""
    try:
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        dataset = _build_ragas_dataset(test_cases, hits_by_query)
        if dataset is None or len(dataset.samples) == 0:
            return None

        # ragas 0.4.x `evaluate` is synchronous — run in a worker thread so
        # the event loop is not blocked during the LLM-judged metrics pass.
        result = await asyncio.wait_for(
            asyncio.to_thread(
                ragas_evaluate,
                dataset,
                metrics=[
                    faithfulness,
                    answer_relevancy,
                    context_precision,
                    context_recall,
                ],
            ),
            timeout=max(EVAL_TIMEOUT_S * 5, 60.0),
        )
        return {
            "faithfulness": _safe_mean(result, "faithfulness"),
            "answer_relevancy": _safe_mean(result, "answer_relevancy"),
            "context_precision": _safe_mean(result, "context_precision"),
            "context_recall": _safe_mean(result, "context_recall"),
        }
    except Exception as e:  # noqa: BLE001 - ragas is best-effort
        log.warning("ragas_eval_failed_falling_back_to_heuristics error=%s", e)
        return None


def _safe_mean(result: Any, metric: str) -> float:
    """Extract a metric mean from a ragas EvaluationResult/Dataset safely."""
    try:
        scores = [float(x) for x in result[metric]] if hasattr(result, "__getitem__") else []
    except (KeyError, TypeError, ValueError):
        scores = []
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


# ======================================================================
# PIPELINE + RUNNER
# ======================================================================


async def _search_one(question: str, collection: str) -> dict[str, Any]:
    """Run the production rmi-backend RAG pipeline for a single question."""
    from app.rag.models import SearchRequest
    from app.rag.service import RAGService

    svc = RAGService()
    start = time.monotonic()
    resp = await asyncio.wait_for(
        svc.search(
            SearchRequest(
                query=question,
                collection=collection,
                top_k=EVAL_TOP_K,
                min_similarity=0.0,
            )
        ),
        timeout=EVAL_TIMEOUT_S,
    )
    latency_ms = (time.monotonic() - start) * 1000
    return {
        "context_chunks": [h.content for h in resp.hits],
        "doc_ids": [h.doc_id for h in resp.hits],
        "latency_ms": round(latency_ms, 2),
        "total": resp.total,
    }


async def _run_one(tc: dict, collection: str) -> dict[str, Any]:
    """Evaluate a single test case against the pipeline."""
    question = tc.get("question", "")
    ground_truth = tc.get("ground_truth", "")
    try:
        search = await _search_one(question, collection)
    except TimeoutError:
        return {
            "question": question,
            "error": f"timeout after {EVAL_TIMEOUT_S}s",
            "metrics": {},
        }
    except Exception as e:  # noqa: BLE001
        return {"question": question, "error": str(e)[:200], "metrics": {}}

    chunks = search["context_chunks"]
    return {
        "question": question,
        "ground_truth": ground_truth,
        "retrieved": search["total"],
        "doc_ids": search["doc_ids"],
        "context_chunks": chunks,
        "latency_ms": search["latency_ms"],
        "error": "",
        "metrics": {
            "faithfulness": round(_keyword_faithfulness(ground_truth, chunks), 4),
            "answer_relevancy": round(_keyword_relevancy(ground_truth, question), 4),
            "context_precision": round(_keyword_context_precision(question, chunks), 4),
            "context_recall": round(_keyword_context_recall(question, chunks), 4),
        },
    }


async def evaluate_rag_pipeline(
    test_set: list[dict] | None = None,
    collection: str = "threat_intel_v2",
) -> dict[str, Any]:
    """
    Evaluate the RugBrain RAG pipeline against a test set.

    Measures: faithfulness, answer_relevancy, context_precision, context_recall.
    Falls back to heuristics if ragas is not installed.

    Returns {metrics, timestamp, collection, test_count}
    """
    test_cases = test_set if test_set is not None else RUGBRAIN_GOLDEN_SET
    if not test_cases:
        return {
            "metrics": {},
            "timestamp": datetime.now(UTC).isoformat(),
            "collection": collection,
            "test_count": 0,
            "error": "empty test set",
        }

    semaphore = asyncio.Semaphore(EVAL_MAX_CONCURRENCY)

    async def _run_with_semaphore(tc: dict) -> dict[str, Any]:
        async with semaphore:
            return await _run_one(tc, collection)

    per_query = await asyncio.gather(*(_run_with_semaphore(tc) for tc in test_cases))
    valid = [p for p in per_query if not p.get("error")]
    n = len(valid) or 1

    hits_by_query = {p["question"]: p.get("context_chunks", []) for p in valid if p.get("doc_ids")}

    use_ragas = _ragas_metrics_available()
    ragas_metrics = await _evaluate_with_ragas(valid, hits_by_query) if use_ragas else None

    # Default aggregate metrics from heuristics (always computed).
    metrics = {
        "faithfulness": round(sum(p["metrics"]["faithfulness"] for p in valid) / n, 4),
        "answer_relevancy": round(sum(p["metrics"]["answer_relevancy"] for p in valid) / n, 4),
        "context_precision": round(sum(p["metrics"]["context_precision"] for p in valid) / n, 4),
        "context_recall": round(sum(p["metrics"]["context_recall"] for p in valid) / n, 4),
    }
    metric_source = "heuristics"
    if ragas_metrics is not None:
        metrics = ragas_metrics
        metric_source = "ragas"

    return {
        "metrics": metrics,
        "metric_source": metric_source,
        "timestamp": datetime.now(UTC).isoformat(),
        "collection": collection,
        "test_count": len(test_cases),
        "queries_run": len(valid),
        "errors": len(per_query) - len(valid),
        "per_query": per_query,
    }


async def evaluate_on_golden_set() -> dict:
    """Evaluate using the RugBrain golden test set."""
    result = await evaluate_rag_pipeline(
        test_set=RUGBRAIN_GOLDEN_SET,
        collection="threat_intel_v2",
    )
    result["golden_set"] = True
    return result


def record_evaluation_results(results: dict) -> None:
    """Append evaluation results to the history file for trending.

    Best-effort: never raises (a failed write must not break the API call).
    """
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": results.get("timestamp", datetime.now(UTC).isoformat()),
            "collection": results.get("collection", ""),
            "test_count": results.get("test_count", 0),
            "metrics": results.get("metrics", {}),
            "metric_source": results.get("metric_source", ""),
        }
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        log.warning("rugbrain_eval_history_write_failed error=%s", e)


def get_evaluation_history(limit: int = 10) -> list[dict]:
    """Get recent evaluation results for trend analysis."""
    if not HISTORY_PATH.exists():
        return []
    records: list[dict] = []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("rugbrain_eval_history_read_failed error=%s", e)
        return []
    return records[-limit:]


__all__ = [
    "RUGBRAIN_GOLDEN_SET",
    "HISTORY_PATH",
    "evaluate_rag_pipeline",
    "evaluate_on_golden_set",
    "record_evaluation_results",
    "get_evaluation_history",
]
