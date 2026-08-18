"""Cross-encoder reranking for the RAG pipeline (Qwen3 Reranker 8B).

Reranks retrieved candidates with Qwen3 Reranker 8B Q4_K_M via llama.cpp
``/rerank`` endpoint (host systemd service, bound to 172.22.0.1:8003).

Fail-open: if the reranker is unreachable, callers keep the pre-rerank order
so search never breaks because the reranker is unavailable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.rag.tracing import trace_span

log = get_logger(__name__)

# Qwen3 Reranker 8B Q4_K_M via llama.cpp on Talos (systemd qwen-reranker.service)
RERANK_URL: str = os.getenv("RERANK_URL", "http://172.22.0.1:8003")


async def rerank_results(query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    """Rerank *documents* against *query* via Qwen3 Reranker 8B.

    Args:
        query: The user query string.
        documents: Candidate document texts (same order as retrieval).
        top_n: Maximum number of ranked results to return.

    Returns:
        List of ``{"index": int, "relevance_score": float}`` dicts ordered
        by relevance descending. Only documents with
        ``relevance_score > 0.0`` are kept (per spec). Empty list on
        failure (fail-open) or when nothing clears the threshold.
    """
    if not query or not documents:
        return []
    with trace_span(
        "rag.rerank.qwen3",
        {
            "openinference.span.kind": "RERANKER",
            "openinference.embedding.model": "Qwen3-Reranker-8B-Q4_K_M",
            "rerank.input_count": len(documents),
            "rerank.top_n": top_n,
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{RERANK_URL.rstrip('/')}/rerank",
                    json={
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_n, len(documents)),
                    },
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            # Filter: only keep docs with relevance_score > 0.0
            kept = [r for r in results if r.get("relevance_score", 0.0) > 0.0]
            span.set_attribute("rerank.result_count", len(kept))
            return kept
        except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
            log.debug("rerank_results_unavailable err=%s", exc)
            return []


def reorder_by_relevance(items: list[Any], reranked: list[dict[str, Any]], top_n: int) -> list[Any]:
    """Reorder *items* (index-aligned with the rerank request) by Qwen3
    relevance, mutating each item's ``final_score`` and
    ``metadata["rerank_score"]``.

    Args:
        items: Retrieved hit objects (duck-typed: need ``final_score`` and
            ``metadata`` attributes).
        reranked: Output of :func:`rerank_results` (``index`` + ``relevance_score``).
        top_n: Truncate the reordered list to this many items.

    Returns:
        Reordered items (``[]`` when nothing survived the ``> 0.0`` filter).
    """
    ordered: list[Any] = []
    for item in sorted(reranked, key=lambda x: -x["relevance_score"]):
        idx = item["index"]
        if 0 <= idx < len(items):
            hit = items[idx]
            score = float(item["relevance_score"])
            hit.final_score = score
            hit.metadata = {**hit.metadata, "rerank_score": round(score, 4)}
            ordered.append(hit)
    return ordered[:top_n]


__all__ = [
    "RERANK_URL",
    "reorder_by_relevance",
    "rerank_results",
]
