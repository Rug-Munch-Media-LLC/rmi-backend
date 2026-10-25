"""Aggregation rules for the /health endpoint.

Split out of ``health_route.py`` (debt wave, 2026-10-25): the endpoint keeps
the probes, this module owns the verdict logic, so each rule is one function.
"""

from __future__ import annotations

from typing import Any


def finalize_rag_pipeline(stores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pop the RAG probe out of the store map and normalise its verdict.

    RAG stats are observability, not a liveness gate. Embed-aware: a degraded
    pipeline (embedder unavailable, or consumers running with no ingestion)
    must NOT report healthy just because the consumers are alive.
    """
    rag = dict(stores.pop("rag_pipeline", {}))
    rag.pop("latency_ms", None)
    if rag.get("status") == "degraded":
        rag["healthy"] = False
    else:
        rag.setdefault(
            "healthy",
            bool(rag.get("poller_running") or rag.get("subscriber_running")),
        )
    rag.setdefault("queue_depth", 0)
    rag.setdefault("ingested_count", 0)
    return rag


def compute_status(stores: dict[str, dict[str, Any]]) -> str:
    """healthy / degraded / unhealthy from per-store verdicts and latency.

    Critical = redis or postgres down. Stores intentionally skipped or
    unavailable do not count against the aggregate; a dependency answering in
    seconds must not leave the aggregate reading "healthy".
    """
    critical_up = stores["redis"].get("healthy") and stores["postgres"].get("healthy")
    graded = [
        s.get("healthy", False)
        for n, s in stores.items()
        if n not in ("redis", "postgres") and s.get("status") not in ("skipped", "unavailable")
    ]
    if not critical_up:
        return "unhealthy"
    if graded and not all(graded):
        return "degraded"
    if any(s.get("latency_state") == "degraded" for s in stores.values()):
        return "degraded"
    return "healthy"
