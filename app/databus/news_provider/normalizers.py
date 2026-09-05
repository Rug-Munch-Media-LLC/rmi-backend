"""Article normalization: dedupe, sorting, and relevance ranking.

Extracted from the background refresh pipeline so the fetch → normalize →
cache flow is explicit. All passes are fail-open: degradation never empties
the feed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger("news_provider")

RELEVANCE_QUERY = (
    "major market-moving crypto news: regulation, hacks, ETFs, "
    "protocol exploits, macro events affecting bitcoin ethereum solana"
)


def dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exact-title dedupe, then fuzzy near-dup collapse.

    Wire services syndicate the same headline with tiny edits; the fuzzy
    pass (difflib ratio > 0.88 on alnum-normalized titles) collapses them.
    """
    import difflib

    def _norm_title(t: str) -> str:
        return "".join(ch for ch in t.lower() if ch.isalnum())

    seen_titles: set[str] = set()
    seen_norms: list[str] = []
    result: list[dict[str, Any]] = []
    for art in articles:
        title = art.get("title", "")
        if not title:
            result.append(art)
            continue
        if title in seen_titles:
            continue
        norm = _norm_title(title)[:120]
        if norm and any(difflib.SequenceMatcher(None, norm, prev).ratio() > 0.88 for prev in seen_norms[-200:]):
            continue
        seen_titles.add(title)
        if norm:
            seen_norms.append(norm)
        result.append(art)
    return result


def sort_by_published(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by published_at (newest first), in place."""
    articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    return articles


async def relevance_rerank(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Relevance pass (fail-open): Qwen3-4B rerank first, embed-cosine
    as fallback while the embed migration cooks. Same-provider cosine
    keeps every tier dimension-correct.
    """
    rerank_done = False
    try:
        from app.rag.rerank import rerank_results

        rerank_cands = articles[:24]
        ranked = await asyncio.wait_for(
            rerank_results(
                RELEVANCE_QUERY,
                documents=[f"{a.get('title', '')}. {str(a.get('description', ''))[:160]}" for a in rerank_cands],
                top_n=len(rerank_cands),
                tier="deep",
            ),
            timeout=45.0,
        )
        if ranked:
            order = {r["index"]: r["relevance_score"] for r in ranked}
            rerank_cands.sort(key=lambda a: order.get(rerank_cands.index(a), 0.0), reverse=True)
            articles = rerank_cands + articles[len(rerank_cands) :]
            logger.info(f"Reranked {len(rerank_cands)} articles via qwen3-reranker-4b")
            rerank_done = True
    except Exception as e:
        logger.debug(f"4B rerank unavailable this cycle: {e}")

    if not rerank_done:
        articles = await _embed_cosine_fallback(articles)
    return articles


async def _embed_cosine_fallback(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Embed-cosine relevance fallback (fail-open)."""
    try:
        import math

        import httpx

        from app.databus.vault import resolve_key

        candidates = articles[:40]
        if not candidates:
            return articles
        docs = [f"{a.get('title', '')}. {str(a.get('description', ''))[:160]}" for a in candidates]

        async def _embed_batch(
            client: httpx.AsyncClient, url: str, inputs: list[str], model: str | None = None
        ) -> list[list[float]]:
            payload: dict[str, object] = {"input": inputs}
            if model:
                payload["model"] = model
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return [d["embedding"] for d in resp.json()["data"]]

        q_vec = d_vecs = None
        async with httpx.AsyncClient(timeout=45) as client:
            try:
                embed_url = "http://127.0.0.1:8004/v1/embeddings"
                q_vec = (await _embed_batch(client, embed_url, [RELEVANCE_QUERY]))[0]
                d_vecs = await _embed_batch(client, embed_url, docs)
            except Exception:
                logger.debug("local embedder unavailable - trying API fallbacks")
            if q_vec is None or d_vecs is None:
                _mkey = resolve_key("MISTRAL_API_KEY").strip()
                if not _mkey:
                    raise RuntimeError("no MISTRAL_API_KEY for embed fallback")
                _murl = "https://api.mistral.ai/v1/embeddings"
                _mh = {"Authorization": f"Bearer {_mkey}"}
                q_resp = await client.post(
                    _murl, json={"model": "mistral-embed", "input": [RELEVANCE_QUERY]}, headers=_mh
                )
                q_resp.raise_for_status()
                q_vec = q_resp.json()["data"][0]["embedding"]
                d_resp = await client.post(_murl, json={"model": "mistral-embed", "input": docs}, headers=_mh)
                d_resp.raise_for_status()
                d_vecs = [d["embedding"] for d in d_resp.json()["data"]]

        def _cos(a: list[float], b: list[float]) -> float:
            num = sum(x * y for x, y in zip(a, b, strict=False))
            da = math.sqrt(sum(x * x for x in a)) or 1.0
            db = math.sqrt(sum(y * y for y in b)) or 1.0
            return num / (da * db)

        scored = sorted(
            range(len(candidates)),
            key=lambda i: _cos(q_vec, d_vecs[i]),
            reverse=True,
        )
        result = [candidates[i] for i in scored] + articles[len(candidates) :]
        logger.info(f"Relevance-ranked {len(candidates)} articles via embed cosine")
        return result
    except Exception as e:
        logger.debug(f"Relevance pass skipped: {e}")
        return articles
