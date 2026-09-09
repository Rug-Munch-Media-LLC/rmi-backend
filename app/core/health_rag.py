"""RAG ingestion pipeline health probe (observability block of GET /health).

Split out of ``app.core.health_route`` (de-godding, behavior-preserving).
``app/core/health_route.py`` keeps a ``_check_rag_pipeline`` wrapper that
injects its own ``_get_http_client`` so existing monkeypatches of
``app.core.health_route._get_http_client`` keep working.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from app.core.health_env import _IS_DEV, _get_url

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

logger = logging.getLogger(__name__)


def _deepinfra_key() -> str:
    """Resolve the DeepInfra API key (env var first, secrets-file fallback).

    Sync helper so the async probe doesn't block the event loop with a file
    open (mirrors app.rag.embeddings / crypto_embeddings providers).
    """
    val = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if val:
        return val
    for path in (
        os.getenv("DEEPINFRA_KEY_FILE", ""),
        "/run/secrets/deepinfra_api_key",
        "/srv/secrets/deepinfra_api_key",
    ):
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                k = fh.read().strip()
            if k:
                return k
        except OSError:
            continue
    return ""


async def check_rag_pipeline(
    client_factory: Callable[[], Awaitable[httpx.AsyncClient]],
) -> dict[str, Any]:
    """RAG ingestion pipeline stats — scanner→RAG queue depth + ingested count.

    The scanner publishes high/critical findings to the ``rmi:rag:ingest``
    pub/sub channel and the ``rmi:rag:findings_queue`` LPUSH list. rugbrain's
    poller/subscriber consume those, embed with Qwen3-4B, and upsert into the
    Qdrant ``threat_intel`` collection. This returns:

    - ``queue_depth``: LLEN of ``rmi:rag:findings_queue`` (backlog waiting to
      be embedded). 0 = poller is keeping up.
    - ``ingested_count``: cumulative count reported by rugbrain-api's
      subscriber + poller (in-memory, resets on rugbrain-api restart).
    - ``poller_running`` / ``subscriber_running``: whether rugbrain's
      background consumers are alive.
    - ``embedding_available``: whether Qwen3 embedder has a usable model
      (qwen3-embedding-4b) loaded.
    - ``reranker_available``: whether Qwen3 reranker is available
      (qwen3-reranker-4b) loaded.
    - ``status``: ``"healthy"`` or ``"degraded"``. Degraded when the embedder
      is unavailable or when consumers run but nothing has ever been ingested.
    - ``issues``: list of degradation reasons (``embedder_unavailable``,
      ``consumers_running_no_ingestion``).

    Fail-open: any Redis/rugbrain-api error is captured in ``error`` and the
    endpoint still returns 200 (pipeline stats are observability, not a
    liveness gate).

    ``client_factory`` is injected by the caller (the health_route shim
    passes its module-level ``_get_http_client``) so the shared-client and
    patching semantics of the original implementation are preserved.
    """
    stats: dict[str, Any] = {
        "queue_depth": 0,
        "ingested_count": 0,
        "poller_running": False,
        "subscriber_running": False,
    }

    # ── Queue depth from Redis (rmi:rag:findings_queue) ──
    try:
        from app.core.redis import get_redis_async

        redis_client = get_redis_async()
        if redis_client is not None:
            depth = await asyncio.wait_for(redis_client.llen("rmi:rag:findings_queue"), timeout=2.0)
            stats["queue_depth"] = int(depth or 0)
    except Exception as exc:
        stats["error"] = f"redis: {str(exc)[:120]}"

    # ── Embedder health (DeepInfra primary, 2026-08-29) ──
    # The canonical embedder is DeepInfra Qwen/Qwen3-Embedding-4B (2560d) with a
    # local :8004 fallback. DeepInfra has no in-container routing problem, so
    # availability is keyed on the resolved API key + a reachable API. The local
    # llama.cpp lane (EMBEDDER_URL) is probed for the reranker only.
    embedding_available = False
    reranker_available = False
    try:
        import httpx

        key = _deepinfra_key()
        if key:
            async with httpx.AsyncClient() as http_client:
                di_resp = await http_client.get(
                    "https://api.deepinfra.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=5,
                )
                embedding_available = di_resp.status_code == 200
        # Reranker is only local (:8003) — probe the llama.cpp lane for it.
        embedder_base_url = _get_url(
            "EMBEDDER_URL",
            "http://127.0.0.1:8004/v1",
            "http://127.0.0.1:8001",
        ).rstrip("/")
        async with httpx.AsyncClient() as http_client:
            mresp = await http_client.get(f"{embedder_base_url}/models", timeout=5)
            model_ids = [m.get("id", "") for m in mresp.json().get("data", [])]
            reranker_available = any("qwen3-reranker" in mid or "reranker" in mid for mid in model_ids)
    except Exception as exc:
        # embedding_available / reranker_available stay False — observability,
        # not a liveness gate. Degrade, don't raise.
        logger.debug("rag_embed_health_probe_failed: %s", exc)
    stats["embedding_available"] = embedding_available
    stats["reranker_available"] = reranker_available

    # ── Ingested count + consumer health from rugbrain-api ──
    ingested_count = 0
    poller_running = False
    subscriber_running = False
    try:
        rag_api_url = os.getenv(
            "RAG_API_URL",
            "http://rugbrain-api:8012" if not _IS_DEV else "http://127.0.0.1:8012",
        ).rstrip("/")
        http_client = await client_factory()
        resp = await asyncio.wait_for(http_client.get(f"{rag_api_url}/health"), timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            sub = data.get("subscriber_stats") or {}
            poll = data.get("poller_stats") or {}
            ingested_count = int(sub.get("ingested_count", 0)) + int(poll.get("ingested_count", 0))
            poller_running = bool(poll.get("running", False))
            subscriber_running = bool(sub.get("running", False))
            stats["ingested_count"] = ingested_count
            stats["poller_running"] = poller_running
            stats["subscriber_running"] = subscriber_running
            stats["rag_api_status"] = data.get("status", "unknown")
        else:
            stats["rag_api_status"] = f"http_{resp.status_code}"
    except Exception as exc:
        stats["error"] = f"{stats.get('error', '')} rag_api: {str(exc)[:120]}".strip()

    # ── Aggregated status ──
    # Embed-aware: an unavailable embedder means the pipeline cannot index
    # anything, so it is degraded even if the consumers are alive.
    status = "healthy"
    issues: list[str] = []
    if not embedding_available:
        status = "degraded"
        issues.append("embedder_unavailable")

    # If consumers are running but nothing has ever been ingested, the
    # pipeline is degraded immediately (no grace period): zero ingestion
    # means the subscribers/poller are alive yet nothing is being indexed.
    if poller_running and subscriber_running and ingested_count == 0:
        status = "degraded"
        issues.append("consumers_running_no_ingestion")

    stats["status"] = status
    stats["issues"] = issues
    return stats
