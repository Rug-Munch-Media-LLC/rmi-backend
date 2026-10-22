"""RugBrain RAG retrieval (4096-d 8B lane) + Qwen3 rerank on :8003.

The rag API owns the embed/collection routing for ``*_8b`` lanes; this
module only speaks to ``POST /api/v1/search/multi`` and then applies the
local cross-encoder so every passage carries a comparable relevance score.

Fail-open: no hits, a non-200, or a dead reranker all return ``[]`` — the
service treats that as "no grounded evidence" and moves to web search.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

_CANDIDATE_POOL = 12
_MIN_TEXT_CHARS = 40

DEFAULT_COLLECTIONS: tuple[str, ...] = (
    "news_v2_8b",
    "defi_exploits_8b",
    "regulation_enforcement_8b",
    "codebase_reference_v2_8b",
)


@dataclass(frozen=True)
class Passage:
    """One citable chunk of grounded evidence."""

    text: str
    title: str
    url: str | None
    library_id: str | None
    source: str
    score: float


def collections() -> list[str]:
    raw = os.getenv("CHAT_RAG_COLLECTIONS", "")
    if raw.strip():
        return [c.strip() for c in raw.split(",") if c.strip()]
    return list(DEFAULT_COLLECTIONS)


def _api_url() -> str:
    return os.getenv("RAG_API_URL", "http://127.0.0.1:8012/api/v1").rstrip("/")


def _api_key() -> str:
    try:
        from app.databus.vault import resolve_key

        return resolve_key("RMI_RAG_API_KEY") or ""
    except Exception:  # noqa: BLE001 — key resolution must never break chat
        return os.getenv("RMI_RAG_API_KEY", "")


def _url_from(hit: dict[str, Any], meta: dict[str, Any]) -> str | None:
    for candidate in (meta.get("url"), meta.get("source_url"), hit.get("url"), hit.get("source")):
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    return None


def _to_passages(hits: list[dict[str, Any]]) -> list[Passage]:
    passages: list[Passage] = []
    for hit in hits:
        text = str(hit.get("content") or hit.get("text") or "").strip()
        if len(text) < _MIN_TEXT_CHARS:
            continue
        meta = hit.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        title = str(meta.get("title") or text[:80]).strip()
        library_id = meta.get("library_id") or meta.get("collection") or hit.get("collection")
        passages.append(
            Passage(
                text=text[:2000],
                title=title[:200],
                url=_url_from(hit, meta),
                library_id=str(library_id) if library_id else None,
                source="rag",
                score=float(hit.get("similarity") or 0.0),
            )
        )
    return passages


async def _search(query: str, limit: int) -> list[dict[str, Any]]:
    body = {
        "query": query,
        "collections": collections(),
        "limit": limit,
        "limit_per_collection": max(limit, 12),
        "rerank": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=3.0)) as client:
            resp = await client.post(
                f"{_api_url()}/search/multi",
                json=body,
                headers={"X-API-Key": _api_key()},
            )
        if resp.status_code != 200:
            log.warning("chat_rag_non_200 status=%s", resp.status_code)
            return []
        return list(resp.json().get("results") or [])
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_rag_failed err=%s", type(exc).__name__)
        return []


async def _rerank(query: str, passages: list[Passage], top_k: int) -> list[Passage]:
    if not passages:
        return []
    try:
        from app.integrations.rag_helpers.rerank import rerank_results

        ranked = await rerank_results(
            query, [p.text for p in passages], top_n=top_k, tier="live"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_rerank_failed err=%s", type(exc).__name__)
        bottom = float(os.getenv("CHAT_RERANK_FLOOR", "0.10"))
        return [p for p in passages[:top_k] if p.score >= bottom]
    out: list[Passage] = []
    for item in ranked or []:
        idx = item.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(passages):
            continue
        base = passages[idx]
        out.append(
            Passage(
                text=base.text,
                title=base.title,
                url=base.url,
                library_id=base.library_id,
                source=base.source,
                score=float(item.get("relevance_score") or 0.0),
            )
        )
    return out


async def retrieve(query: str, top_k: int = 6) -> list[Passage]:
    """Return up to ``top_k`` reranked passages; ``[]`` when nothing is grounded."""
    hits = await _search(query, _CANDIDATE_POOL)
    passages = _to_passages(hits)
    if not passages:
        return []
    return await _rerank(query, passages[: min(len(passages), _CANDIDATE_POOL)], top_k)
