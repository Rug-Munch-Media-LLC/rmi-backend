"""SearXNG web search for chat grounding (current-affairs fallback).

Powers the chat brain's current-events routing: when RAG returns no hits, or
the user asks about something news-y, we query the self-hosted SearXNG
instance (JSON API, no auth) and inject the top hits into the system prompt
as a "Web results (live)" context block — own data (RAG) first, live web
second.

Fail-open contract: any error (network, timeout, non-200, bad JSON, Redis
down) returns ``[]`` so chat never breaks because web search is unavailable.
Results are cached in Redis for 10 minutes to keep SearXNG traffic low.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


def _searxng_url() -> str:
    """Resolve the SearXNG base URL from settings (fallback: env var, localhost)."""
    try:
        from app.config import settings as _s

        if _s.searxng_url:
            return _s.searxng_url.rstrip("/")
    except Exception:  # noqa: S110 — optional dependency during boot
        pass
    return os.getenv("SEARXNG_URL", "http://localhost:8089").rstrip("/")


SEARXNG_TIMEOUT = 8.0  # seconds — live web must never stall chat
SEARXNG_CACHE_TTL = 600  # 10 minutes
SEARXNG_USER_AGENT = "RugMunchIntelligence/1.0"


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(query.encode("utf-8", "ignore")).hexdigest()[:16]
    return f"searxng:{digest}"


def _parse_results(payload: dict[str, Any], max_results: int) -> list[dict[str, str]]:
    """Map SearXNG JSON results to ``[{title, url, content}]`` (max N)."""
    results: list[dict[str, str]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title and not url:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "content": (item.get("content") or "").strip(),
            }
        )
        if len(results) >= max_results:
            break
    return results


async def searxng_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search SearXNG and return up to ``max_results`` items.

    Returns ``[{title, url, content}, ...]``. Never raises — on any error it
    returns ``[]`` so callers fall through without web context (web search is
    an enhancement, never a gate). Non-empty results are cached in Redis for
    10 minutes; empty results are not cached so a SearXNG outage is not
    pinned by a stale cache entry.
    """
    query = (query or "").strip()
    if not query:
        return []

    # Redis cache lookup (best-effort — a Redis failure just skips the cache).
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is not None:
            cached = await r.get(_cache_key(query))
            if cached:
                try:
                    parsed = json.loads(cached)
                    if isinstance(parsed, list):
                        return parsed[:max_results]
                except (TypeError, json.JSONDecodeError):
                    pass
    except Exception:  # noqa: S110 — cache is best-effort
        pass

    try:
        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            resp = await client.get(
                f"{_searxng_url()}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": SEARXNG_USER_AGENT},
            )
        if resp.status_code != 200:
            logger.warning("SearXNG search failed: status=%s query=%.60s", resp.status_code, query)
            return []
        payload = resp.json()
    except Exception as e:  # fail open — web search is an enhancement, never a gate
        logger.warning("SearXNG search error (query=%.60s): %s", query, e)
        return []

    results = _parse_results(payload, max_results)

    # Cache non-empty results only.
    if results:
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r is not None:
                await r.setex(_cache_key(query), SEARXNG_CACHE_TTL, json.dumps(results))
        except Exception:  # noqa: S110 — cache is best-effort
            pass
    return results
