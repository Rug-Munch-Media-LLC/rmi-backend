"""RAG + tool grounding for the chat router.

Moved from ``app/routers/chat/__init__.py`` (de-godding, behavior-preserving).
Contains the per-turn RAG search memo, the per-collection RAG search facade,
the aggregated RAG context search, and the tool-use context fetcher.

PATCHING CONTRACT: tests patch ``app.routers.chat._rag_search_collection`` /
``_search_rag_context`` / ``_fetch_tool_context`` on the PACKAGE module. The
internal call sites below therefore resolve those names through the package
module (``chat_pkg``) at call time instead of binding them at import time.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from app.core.logging import get_logger

from ._rag import (
    _CACHE_MAX_ENTRIES,
    RAG_COLLECTIONS,
    RAG_SEARCH_TIMEOUT,
    RAG_TOKEN_COLLECTIONS,
    RAG_TOP_K,
    _build_rag_query,
    _extract_token_hints,
    _format_rag_context,
    _rag_dedup_key,
)

logger = get_logger(__name__)

# Package module — patched attributes (see docstring) resolve at call time.
from app.routers import chat as chat_pkg  # noqa: E402  (circular by design)

# ── Per-turn RAG search memo (one search+embed per (collection, query)) ──
# The chat handler fans the SAME RAG query out to every collection in
# RAG_COLLECTIONS; each ``_rag_search_collection`` call routes through
# ``RAGService().search`` → ``three_pillar_search`` → ``get_embedding``.
# The engine's ``CachedEmbedder`` already dedupes identical query TEXT across
# collections (md5-keyed, in-memory + Redis, TTL 86400 — verified in
# ``app/rag/embedder.py``), so the same expanded query is only truly embedded
# once per process. This memo adds a per-turn boundary cache on top: the same
# (collection, query) is never searched (QueryExpander + Qdrant round-trip)
# twice within the window — guarding alias collisions, duplicate collection
# entries, and burst traffic on the same token. Only NON-EMPTY results are
# cached, so a transient RAG outage self-heals instead of being masked by a
# cached empty result.
_RAG_MEMO_TTL = 60.0  # seconds — same freshness budget as the market block
_rag_memo: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def _rag_memo_get(query: str, collection: str, top_k: int) -> list[dict[str, Any]] | None:
    """Return memoized hits for (collection, query, top_k) within TTL, else None."""
    entry = _rag_memo.get((query, collection, top_k))
    if entry is None:
        return None
    expires_at, hits = entry
    if time.monotonic() > expires_at:
        _rag_memo.pop((query, collection, top_k), None)
        return None
    return hits


def _rag_memo_set(query: str, collection: str, top_k: int, hits: list[dict[str, Any]]) -> None:
    """Memoize non-empty hits; evict oldest when the memo exceeds the cap."""
    if not hits:
        return
    _rag_memo[(query, collection, top_k)] = (time.monotonic() + _RAG_MEMO_TTL, hits)
    if len(_rag_memo) > _CACHE_MAX_ENTRIES:
        overflow = len(_rag_memo) - _CACHE_MAX_ENTRIES
        oldest_keys = sorted(_rag_memo, key=lambda k: _rag_memo[k][0])[:overflow]
        for k in oldest_keys:
            _rag_memo.pop(k, None)


async def _rag_search_collection(query: str, collection: str, top_k: int = RAG_TOP_K) -> list[dict[str, Any]]:
    """Search a single RAG collection (in-process facade — same engine as POST /api/v1/rag/v2/search).

    Returns hits as dicts with keys ``content``/``score``/``metadata``/
    ``collection``/``doc_id``. Returns ``[]`` on any failure so callers fall
    through without RAG context. The import is lazy so the chat router still
    mounts if the RAG stack fails to import.

    Per-turn memo: an identical (collection, query) searched within
    ``_RAG_MEMO_TTL`` returns the cached hits without re-running the search
    pipeline (and thus without re-embedding the query). See the memo
    definition above for the freshness rationale.
    """
    memo_hits = _rag_memo_get(query, collection, top_k)
    if memo_hits is not None:
        return memo_hits
    try:
        from app.rag import RAGService, SearchRequest

        resp = await RAGService().search(SearchRequest(query=query, collection=collection, top_k=top_k))
        hits = [
            {
                "content": h.content,
                "score": h.score,
                "metadata": h.metadata,
                "collection": h.collection or collection,
                "doc_id": h.doc_id,
            }
            for h in resp.hits
        ]
    except Exception as e:  # RAG is best-effort — never break chat on search failure
        logger.debug("RAG search failed for collection=%s: %s", collection, e)
        return []
    _rag_memo_set(query, collection, top_k, hits)
    return hits


async def _search_rag_context(message: str) -> tuple[str, int]:
    """Search RAG collections for context relevant to ``message``.

    Returns ``(formatted_context, hit_count)`` where ``hit_count`` is the
    REAL number of unique relevant hits found across ALL searched collections
    (not a slice): the prompt only gets the top ``RAG_TOP_K`` formatted, but
    the count reports every deduped match so ``X-RAG-Context`` is honest.
    Never raises: on any failure or timeout it returns ``("", 0)`` so chat
    proceeds without RAG context.

    Smart search:
    - Extracts token addresses / ``$SYMBOL`` mentions from the message.
    - Searches ``RAG_COLLECTIONS`` for every message; adds
      ``RAG_TOKEN_COLLECTIONS`` (scanner_v2) when a token address is present.
    - Parallel per-collection search, deduped by content.
    """
    addresses, symbols = _extract_token_hints(message)
    collections = list(RAG_COLLECTIONS)
    if addresses:
        collections.extend(RAG_TOKEN_COLLECTIONS)
    query = _build_rag_query(message, addresses, symbols)

    # ── Per-turn query-embedding warm (ONE embed per turn, not per collection) ──
    # The fan-out below fires N concurrent searches with the SAME query text.
    # Each search embeds the (expanded) query via the engine's CachedEmbedder,
    # which dedupes identical text — BUT ``asyncio.gather`` starts all N at
    # once, so a cold query would miss the memory cache simultaneously and
    # fire N duplicate embed HTTP calls. Warm the embedder once, serially,
    # with the exact text the engine will embed (QueryExpander is
    # deterministic) so every concurrent search hits the cache. Fail-open:
    # embedder down → searches fail the same way they would have anyway.
    try:
        from app.rag.embeddings import get_embedding

        # Lightweight inline query expansion (synonyms + variants) so the
        # embedder warms the cache for the upcoming concurrent searches.
        _q = query.strip()
        _synonyms = {
            "hack": "exploit attack breach",
            "rug": "exit scam pull",
            "honeypot": "can't sell trapped buy",
            "phish": "fake scam",
            "dump": "sell drop crash",
            "pump": "buy surge",
        }
        _extra = []
        for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", _q.lower()):
            if t in _synonyms:
                _extra.append(_synonyms[t])
        if _extra:
            _q = f"{_q} {' '.join(_extra)}"
        await asyncio.wait_for(get_embedding(_q), timeout=1.5)
    except Exception as e:  # RAG is best-effort — warm failure never breaks chat
        logger.debug("RAG query embed warm skipped: %s", e)

    async def _search_all() -> list[dict[str, Any]]:
        # Resolve through the package module so tests patching
        # ``app.routers.chat._rag_search_collection`` are honored.
        results = await asyncio.gather(
            *(chat_pkg._rag_search_collection(query, c) for c in collections),
            return_exceptions=True,
        )
        hits: list[dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                hits.extend(res)
        return hits

    try:
        hits = await asyncio.wait_for(_search_all(), timeout=RAG_SEARCH_TIMEOUT)
    except Exception as e:  # RAG is best-effort — never break chat on timeout/failure
        logger.debug("RAG context search failed/timed out (query=%.60s): %s", message, e)
        return "", 0

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in sorted(hits, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        # Verdict-aware dedup key (full normalized content + verdict/severity):
        # a CLEAN and a MALICIOUS snippet that share identical code MUST both
        # surface — they carry opposite security meaning. The old 100-char
        # prefix key also merged unrelated docs that merely shared a prefix.
        key = _rag_dedup_key(h)
        if key and key not in seen:
            seen.add(key)
            deduped.append(h)
    if not deduped:
        return "", 0
    # Report the REAL number of unique hits across all searched collections
    # (not just the top_k slice we format into the prompt). X-RAG-Context uses
    # this count so callers can show an honest "grounded in N sources" footer.
    total_hits = len(deduped)
    # Format ALL deduped hits (not just ``[:RAG_TOP_K]``): the formatter ranks
    # by collection priority (security_patterns / contract_security first) and
    # its own 1800-char budget is the real cap — a security_patterns hit that
    # ranked 6th by raw score would otherwise be sliced away before the
    # priority ranking could surface it.
    return _format_rag_context(deduped), total_hits


async def _fetch_tool_context(message: str) -> str | None:
    """Detect tool intent and run the executor. Returns live data or None."""
    # Resolve through the package module so tests patching
    # ``app.routers.chat._detect_tool_intent`` / ``_run_tool_for_intent`` are honored.
    intent = chat_pkg._detect_tool_intent(message)
    if intent is None:
        return None
    return await chat_pkg._run_tool_for_intent(message, intent)
