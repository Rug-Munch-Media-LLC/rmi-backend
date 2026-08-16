"""

AI Chat API
===========
Intelligent crypto chatbot interface for Rug Munch Intelligence.

Features:
- Multi-provider AI routing (via existing ai_router)
- Context from user profile and current market data
- Streaming SSE responses
- Chat history stored in SQLite + Redis

Routes:
- POST /api/v1/chat - Send message, get AI response
- POST /api/v1/chat/stream - Streaming chat endpoint (SSE)
- GET /api/v1/chat/history - User's chat history
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai_router import (
    chat_completion as do_chat_completion,
    stream_chat_completion as do_stream_chat_completion,
)
from app.auth import get_current_user
from app.core.errors import AuthorizationError, ExternalAPIError
from app.core.logging import get_logger
from app.core.web_search import searxng_search

# ── Tool-use layer (live data grounding) ──
from app.domains.chat_tools import _detect_tool_intent, _run_tool_for_intent

# ── AI Security Guard ──
from app.security.chat_guardrails import get_chat_rate_limiter

from ..chat_db import _get_db
from ..chat_history import router as chat_history_router

# ── Extracted pure helpers ──
# Message-building + shared ai_guard live in ._messages; prompt text and the
# fast-path detectors (identity / vague / web-search) live in ._prompts.
# Names are re-exported here so every existing importer and test keeps
# working against the SAME objects (no behavior change).
from ._messages import (
    CONTEXT_MAX_MESSAGES,  # noqa: F401  (re-export for existing importers)
    _build_messages,
    _normalize_history,  # noqa: F401  (re-export for existing importers)
    _sanitize_history,  # noqa: F401  (re-export for existing importers)
    ai_guard,
)
from ._prompts import (
    _EVM_ADDRESS_RE,  # noqa: F401  (re-export for existing importers)
    _IDENTITY_ANSWER,  # noqa: F401  (re-export for existing importers)
    _IDENTITY_RE,  # noqa: F401  (re-export for existing importers)
    _SOLANA_ADDRESS_RE,  # noqa: F401  (re-export for existing importers)
    _SYMBOL_RE,  # noqa: F401  (re-export for existing importers)
    _VAGUE_REFERENTS,  # noqa: F401  (re-export for existing importers)
    DISCLAIMER,
    SYSTEM_PROMPT,  # noqa: F401  (re-export for existing importers)
    _identity_answer,
    _looks_like_current_events,  # noqa: F401  (re-export for existing importers)
    _should_web_search,
    _vague_question,
)
from ._rag import (
    _CACHE_MAX_ENTRIES,
    _CACHE_TTL_DEFAULT,
    _CACHE_TTL_MARKET,
    _MAX_TOKEN_HINTS,  # noqa: F401  (re-export for existing importers)
    RAG_COLLECTIONS,
    RAG_CONTEXT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    RAG_HIT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    RAG_SEARCH_TIMEOUT,
    RAG_TOKEN_COLLECTIONS,
    RAG_TOP_K,
    TOOL_RESULT_LABEL,  # noqa: F401  (re-export for existing importers)
    WEB_CONTEXT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    WEB_HIT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    _build_rag_query,
    _cache_key,
    _extract_token_hints,
    _format_rag_context,
    _format_web_context,
    _inject_tool_result,
    _is_market_query,
    _rag_dedup_key,
    _route_task,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# ── History / stats sub-router (extracted) ──
router.include_router(chat_history_router)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import redis.asyncio as redis


# ── Free API endpoints for market context ──
COINGECKO_API = "https://api.coingecko.com/api/v3"

# ── Redis ──
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

_redis: redis.Redis | None = None

CACHE_TTL = 300  # 5 minutes

_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "RugMunchIntelligence/1.0"},
        )
    return _http_client
    logger.warning("chat_db_init_failed", exc_info=True)


# ── Chat rate limiting (Part 3: 30/min, 200/hour per IP) ──
def _client_ip(request: Request) -> str:
    """Best-effort client IP: first X-Forwarded-For hop, else peer IP."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def chat_rate_limit(request: Request) -> None:
    """Per-IP chat rate limit: 30 msgs/min, 200 msgs/hour. Returns 429 + Retry-After."""
    limiter = get_chat_rate_limiter()
    allowed, headers = await limiter.allow(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Slow down and try again later.",
            headers={k: str(v) for k, v in headers.items()},
        )
    request.state.chat_rate_limit_headers = headers


def _apply_rate_limit_headers(response: Response, request: Request) -> None:
    """Attach X-RateLimit-* headers from the dependency to the response."""
    headers = getattr(request.state, "chat_rate_limit_headers", None)
    if headers:
        for k, v in headers.items():
            response.headers[k] = str(v)


# ── Models ──
# Chat answers are short prose — a 320-token budget keeps generation latency
# snappy on the free remote chat tier (measured 2026-08-15 on the old CPU
# host: generation dominated total latency, so the token budget IS the
# latency lever). The default can be raised at deploy time via
# RMI_CHAT_MAX_TOKENS (clamped to 64..2048 — the Pydantic Field bounds stay
# 64..8192 for explicit requests).
# 2026-08-14: raised 512 -> 1536 — 512 was truncating long educational answers
# (e.g. "how to learn TA") mid-sentence for the bot, which sends no max_tokens.
# 2026-08-15: 1536 -> 768 — context fetches (tool/RAG/web) now run in parallel
# via asyncio.gather and the tool-result block is capped at 1200 chars, so the
# prompt stays lean while the model gets fresher grounding; 768 keeps long
# educational answers from truncating while bounding generation latency.
# 2026-08-15: 768 -> 320 — the bot sends max_tokens explicitly (768) and the
# 4B local model needed 87-136s per answer on CPU, blowing past the client
# timeout and the bot's 60s backend timeout, forcing the slow fallback chain.
# 320 tokens ≈ 1300 chars — a full Telegram answer for most queries (long
# answers are chunked bot-side at 3800 chars). Long educational answers may
# truncate at 320; raise per-deploy via RMI_CHAT_MAX_TOKENS if needed.
_CHAT_MAX_TOKENS_BASE = 320
_CHAT_MAX_TOKENS_MIN = 64
_CHAT_MAX_TOKENS_MAX = 2048


def _resolve_chat_max_tokens_default(env_value: str | None = None) -> int:
    """Resolve the ChatRequest ``max_tokens`` default.

    Returns ``_CHAT_MAX_TOKENS_BASE`` (512) unless ``RMI_CHAT_MAX_TOKENS`` is
    set to a parseable int, in which case the value is clamped to
    ``64..2048``. Unparseable values fall back to the base default.
    """
    raw = os.getenv("RMI_CHAT_MAX_TOKENS") if env_value is None else env_value
    if raw is None or not raw.strip():
        return _CHAT_MAX_TOKENS_BASE
    try:
        parsed = int(raw)
    except ValueError:
        return _CHAT_MAX_TOKENS_BASE
    return max(_CHAT_MAX_TOKENS_MIN, min(parsed, _CHAT_MAX_TOKENS_MAX))


CHAT_MAX_TOKENS_DEFAULT = _resolve_chat_max_tokens_default()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None, description="Specific model to use")
    tier: str = Field(default="T2", pattern="^(T0|T1|T2|T3|T4)$", description="AI model tier")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=CHAT_MAX_TOKENS_DEFAULT, ge=64, le=8192)
    context_override: dict[str, Any] | None = Field(
        default=None, description="Custom context injection. `system_context` (str) is appended to the system prompt."
    )
    history: list[dict[str, str]] | None = Field(
        default=None,
        description=(
            "Explicit conversation history (user/assistant messages). "
            "When provided it OVERRIDES the DB-loaded history for this request "
            "(used by trusted server-side callers such as the Telegram bot, "
            "which must not read the shared anonymous history bucket)."
        ),
    )


class StreamRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    model: str | None = Field(default=None)
    tier: str = Field(default="T2", pattern="^(T0|T1|T2|T3|T4)$")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=64, le=8192)


# ── Market context fetcher ──
async def _fetch_market_context() -> dict[str, Any]:
    """Fetch live market data for AI context injection."""
    try:
        client = await _get_client()
        resp = await client.get(
            f"{COINGECKO_API}/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "btc_price": data.get("bitcoin", {}).get("usd", 0),
                "btc_change_24h": data.get("bitcoin", {}).get("usd_24h_change", 0),
                "eth_price": data.get("ethereum", {}).get("usd", 0),
                "eth_change_24h": data.get("ethereum", {}).get("usd_24h_change", 0),
                "sol_price": data.get("solana", {}).get("usd", 0),
                "sol_change_24h": data.get("solana", {}).get("usd_24h_change", 0),
            }
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"CoinGecko price fetch failed: {e}")
    return {}


# ── RAG context grounding ───────────────────────────────────────────────
# Web chat grounds the LLM with relevant RAG knowledge using the same
# pattern as the Telegram bot's RAG+LLM synthesis (bot-side
# ``tools/rag_lookup.py`` + ``handlers/chat_ai/_llm.py``): search RAG before
# the LLM call, format the top hits, and inject them into the system prompt
# via the ``context_override.system_context`` mechanism (see
# ``_build_messages``).
#
# Collections searched for every message; ``scanner_v2`` is added when the
# message mentions a token address. Focused on SECURITY + INTELLIGENCE +
# NEWS + REGULATION + CODE-SECURITY + DEEP KNOWLEDGE — the bot's domain.
# Only REAL, populated Qdrant collections are fanned out (see
# ``app/routers/chat/_rag.py``): legacy P1-KB names with no Qdrant
# collection (``wallet_profiles`` / ``token_analysis`` / ``forensic_reports``)
# and currently-empty collections (``scanned_tokens`` / ``whale_transfers``)
# are NOT searched per-message. When other code paths request those names
# directly, ``app.rag._qdrant._resolve_qdrant_collection`` maps them to the
# live corpus via ``_COLLECTION_ALIASES`` and every hit is stamped with the
# ACTUAL resolved collection (``metadata["resolved_collection"]``), so the
# ``Source:`` labels are truthful.
# Search is best-effort: any failure or timeout returns empty context and
# chat proceeds without RAG (graceful degradation — RAG is an enhancement,
# never a gate).


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
        from app.rag.retrieval import QueryExpander

        await asyncio.wait_for(get_embedding(QueryExpander().expand(query)), timeout=1.5)
    except Exception as e:  # RAG is best-effort — warm failure never breaks chat
        logger.debug("RAG query embed warm skipped: %s", e)

    async def _search_all() -> list[dict[str, Any]]:
        results = await asyncio.gather(
            *(_rag_search_collection(query, c) for c in collections),
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


# ── Live web search routing (current-affairs fallback) ────────────────
# When RAG returns no hits — or the user is clearly asking about something
# news-y — we also query the self-hosted SearXNG instance and inject the top
# hits as a "Web results (live)" context block. Own data (RAG) stays first
# in the context; live web is second. Web search is best-effort and shares
# the chat call's rate-limit budget (no separate bucket). The
# ``_should_web_search`` / ``_looks_like_current_events`` detectors live in
# ._prompts (pure text heuristics).
# ── Complexity-based model routing ─────────────────────────────────────
# Pure-function router: analytical / forensic / explanation-heavy questions
# ("why", "explain", "investigate", on-chain terms, long messages) go to the
# stronger deep (long) tier; simple definitional / quick questions stay on
# the fast (snappy) tier (4x cheaper/faster — the speedup matters for
# trivial asks, while hard questions get the model with enough reasoning
# power).
# ── Helper to save message ──
def _save_message(user_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> Any:
    msg_id = f"msg-{uuid.uuid4().hex[:10]}"
    conn = _get_db()
    conn.execute(
        "INSERT INTO chat_messages (id, user_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
        (msg_id, user_id, role, content, json.dumps(metadata or {})),
    )
    conn.commit()
    conn.close()
    return msg_id


# ── Response cache (speed for repeats) ─────────────────────────────────
# Small in-process TTL cache keyed on the normalized message. Repeated/common
# questions (e.g. "what is solana", "btc price") short-circuit the LLM
# round-trip entirely. Purely in-memory: no Redis, plain dict +
# ``time.monotonic``. NEVER caches the identity/vague canned answers (those
# fast-paths return before the cache is consulted) and NEVER caches LLM
# failures (only successful responses are stored).
_chat_cache: dict[str, tuple[float, str]] = {}


def _cache_get(key: str) -> str | None:
    """Return the cached response for ``key``, or None on miss/expiry."""
    entry = _chat_cache.get(key)
    if entry is None:
        return None
    expires_at, response = entry
    if time.monotonic() > expires_at:
        _chat_cache.pop(key, None)
        return None
    return response


def _cache_set(key: str, response: str, ttl: int) -> None:
    """Store ``response`` under ``key`` with a ``ttl`` (seconds).

    Evicts the oldest entries when the dict exceeds ``_CACHE_MAX_ENTRIES``.
    """
    _chat_cache[key] = (time.monotonic() + ttl, response)
    if len(_chat_cache) > _CACHE_MAX_ENTRIES:
        # Oldest = earliest expiry timestamp (monotonic, so insertion order).
        overflow = len(_chat_cache) - _CACHE_MAX_ENTRIES
        oldest_keys = sorted(_chat_cache, key=lambda k: _chat_cache[k][0])[:overflow]
        for k in oldest_keys:
            _chat_cache.pop(k, None)


# ── Tool-use layer (live data grounding) ───────────────────────────────
# Price/scan/TA/market questions must be grounded in real numbers instead of
# hallucinated ones. _fetch_tool_context detects the tool intent, runs the
# matching executor (best-effort, 8s timeout — see app/domains/chat_tools.py)
# and returns the raw tool result; the route injects it into the system
# context via _inject_tool_result so the LLM answers from live data. When
# there is no intent, or the executor failed/timed out, this returns None and
# chat proceeds with normal RAG/LLM — tools never block or break chat.
async def _fetch_tool_context(message: str) -> str | None:
    """Detect tool intent and run the executor. Returns live data or None."""
    intent = _detect_tool_intent(message)
    if intent is None:
        return None
    return await _run_tool_for_intent(message, intent)


# ── Routes ─────────────────────────────────────────────────────


@router.post("")
async def chat(
    req: ChatRequest,
    request: Request,
    response: Response,
    user: dict[str, Any] | None = Depends(get_current_user),
    _: None = Depends(chat_rate_limit),
) -> dict[str, Any]:
    """Send a message and get an AI response (non-streaming)."""
    user_id = user.get("id", "anon") if user else "anon"

    # ── SECURITY LAYER 1: Input sanitization ──
    raw_message = req.message
    sanitized = ai_guard.sanitize_input(raw_message)
    if sanitized != raw_message:
        req.message = sanitized  # Use sanitized version

    # ── SECURITY LAYER 2: Attack vector scan ──
    scan = ai_guard.scan_input(req.message, session_id=user_id)
    if not scan.safe:
        ai_guard.record_block(
            user_id, message=req.message, reason=scan.reason, category=scan.details.get("category", "")
        )
        raise AuthorizationError(message=f"Security alert: {scan.reason}")

    # ── IDENTITY FAST-PATH ──
    # Canned persona answer for identity/self questions — no RAG, no web
    # search, no LLM round-trip. History is still saved so the conversation
    # stays consistent for follow-ups.
    identity_answer = _identity_answer(req.message)
    if identity_answer is not None:
        _save_message(user_id, "user", req.message)
        _save_message(
            user_id,
            "assistant",
            identity_answer,
            {"model": "canned", "provider": "identity", "security_scanned": True, "rag_context_count": 0},
        )
        _apply_rate_limit_headers(response, request)
        response.headers["X-RAG-Context"] = "0"
        return {
            "id": f"resp-{uuid.uuid4().hex[:8]}",
            "message": identity_answer,
            "model": "canned",
            "provider": "identity",
            "latency_ms": 0,
            "usage": {},
            "x_rag_context": 0,
        }

    # ── VAGUE-QUESTION FAST-PATH ──
    # Underspecified questions ("what happened with the one token the other
    # day") get a clarification prompt — no RAG/web/LLM round-trip, no risk of
    # a degenerate "- " answer from the model.
    vague_answer = _vague_question(req.message)
    if vague_answer is not None:
        _save_message(user_id, "user", req.message)
        _save_message(
            user_id,
            "assistant",
            vague_answer,
            {"model": "canned", "provider": "vague", "security_scanned": True, "rag_context_count": 0},
        )
        _apply_rate_limit_headers(response, request)
        response.headers["X-RAG-Context"] = "0"
        return {
            "id": f"resp-{uuid.uuid4().hex[:8]}",
            "message": vague_answer,
            "model": "canned",
            "provider": "vague",
            "latency_ms": 0,
            "usage": {},
            "x_rag_context": 0,
        }

    # ── RESPONSE CACHE (repeats) ──
    # Repeated/common questions short-circuit the LLM round-trip entirely.
    # The cache key is the (sanitized) message PLUS a fingerprint of any
    # caller-supplied grounding (context_override.system_context — e.g. the
    # Telegram bot's persona). Previously a grounded request bypassed the
    # cache unconditionally, so every bot turn paid a full 56-88s LLM call
    # instead of an 11ms cache hit; now the same grounding → same key →
    # cache hit, while a DIFFERENT persona/RAG block never serves a stale
    # cached answer. Identity/vague canned answers above already returned,
    # so those are never cached. Failures are never cached either (we only
    # store after a successful LLM response below).
    client_grounded = bool(
        req.context_override
        and isinstance(req.context_override, dict)
        and isinstance(req.context_override.get("system_context"), str)
        and req.context_override.get("system_context", "").strip()
    )
    grounding_text = req.context_override.get("system_context") if client_grounded and req.context_override else None
    cache_key = _cache_key(req.message, grounding=grounding_text)
    cached = _cache_get(cache_key)
    if cached is not None:
        _save_message(user_id, "user", req.message)
        _save_message(
            user_id,
            "assistant",
            cached,
            {"model": "cached", "provider": "cache", "security_scanned": True, "rag_context_count": 0},
        )
        _apply_rate_limit_headers(response, request)
        response.headers["X-RAG-Context"] = "0"
        return {
            "id": f"resp-{uuid.uuid4().hex[:8]}",
            "message": cached,
            "model": "cached",
            "provider": "cache",
            "latency_ms": 0,
            "usage": {},
            "x_rag_context": 0,
        }

    # Get or create user context
    user_context = ""
    if user:
        tier = user.get("tier", "free")
        display = user.get("display_name", user.get("email", "detective"))
        user_context = f"User: {display} (tier: {tier})"

    # ── TOOL-USE LAYER (live data) ──
    # Price/scan/TA/market questions get real numbers injected into the
    # system context so the LLM grounds its answer instead of hallucinating.
    # Best-effort: failed/timeout executors return None and chat proceeds
    # with normal RAG/LLM below — tools never block or break chat.
    #
    # ── RAG grounding (non-blocking) ──
    # Direct web chat callers get automatic RAG grounding: search the RAG
    # collections for context relevant to the message and inject the top
    # hits into the system prompt before the LLM call. If the caller already
    # supplied explicit system context (e.g. the Telegram bot sends its own
    # RAG search via context_override.system_context), respect that instead —
    # no double-grounding. RAG search is best-effort: on failure/timeout it
    # returns empty and chat proceeds without context (never blocks chat).
    client_system_context: str | None = None
    if req.context_override and isinstance(req.context_override, dict):
        cs = req.context_override.get("system_context")
        if isinstance(cs, str) and cs.strip():
            client_system_context = cs

    # Current-events queries are owned by the live web-search wiring: a
    # generic tool result (e.g. the news headline feed) must not shadow
    # query-specific RAG + live web results. For those queries RAG + web
    # search run even when the tool layer returned data; for data queries
    # (price/scan/ta/market/...) the tool result IS the grounding, so RAG
    # and web search are skipped to avoid double-grounding.
    current_events_query = _should_web_search(req.message, 0)

    # The tool / RAG / web context fetches used to run sequentially; they now
    # run concurrently via asyncio.gather. RAG joins the parallel batch only
    # for current-events queries, where it runs regardless of the tool result
    # — for data queries its gating depends on the tool result (a live tool
    # result suppresses RAG/web to avoid double-grounding), so it runs after
    # the tool fetch completes below.
    rag_runs_unconditionally = not client_system_context and current_events_query

    async def _safe_tool_fetch() -> str | None:
        try:
            return await _fetch_tool_context(req.message)
        except Exception as e:  # defensive: never let the tool layer break chat
            logger.debug("chat tool fetch raised in handler: %s", e)
            return None

    async def _safe_rag_fetch() -> tuple[str, int]:
        try:
            return await _search_rag_context(req.message)
        except Exception as e:  # defensive: never let RAG break chat
            logger.debug("RAG context search raised in chat handler: %s", e)
            return "", 0

    async def _web_fetch() -> tuple[str, int]:
        # SearXNG runs ONLY for queries that explicitly look like current
        # events (news-ish keywords) — never as a blanket fallback when RAG
        # comes up empty (that previously leaked irrelevant search-result
        # lists into the answer). Best-effort: any failure returns no web
        # context and chat proceeds.
        try:
            if _should_web_search(req.message, 0):
                web_results = await searxng_search(req.message)
                if web_results:
                    return _format_web_context(web_results), len(web_results)
        except Exception as e:  # defensive: never let web search break chat
            logger.debug("Web search raised in chat handler: %s", e)
        return "", 0

    async def _empty_fetch() -> tuple[str, int]:
        return "", 0

    tool_result, rag_out, web_out = await asyncio.gather(
        _safe_tool_fetch(),
        _safe_rag_fetch() if rag_runs_unconditionally else _empty_fetch(),
        _web_fetch() if (not client_system_context and current_events_query) else _empty_fetch(),
        return_exceptions=True,
    )
    # The safe wrappers never raise, but return_exceptions=True widens the
    # static types to BaseException — normalize for the gating below.
    if isinstance(tool_result, BaseException):
        tool_result = None

    # Re-apply the original gating on the parallel results: a live tool result
    # IS the grounding for data queries, so RAG + web context fetched in
    # parallel are discarded (current-events queries keep both).
    if tool_result and not current_events_query:
        rag_context, rag_count = "", 0
        web_context, web_count = "", 0
    else:
        rag_context, rag_count = rag_out if isinstance(rag_out, tuple) else ("", 0)
        web_context, web_count = web_out if isinstance(web_out, tuple) else ("", 0)

    # RAG that could not join the parallel batch (data queries: gated on the
    # tool result) runs now — a tool miss still grounds the answer in RAG.
    if not rag_runs_unconditionally and not client_system_context and not tool_result:
        try:
            rag_context, rag_count = await _search_rag_context(req.message)
        except Exception as e:  # defensive: never let RAG break chat
            logger.debug("RAG context search raised in chat handler: %s", e)
            rag_context, rag_count = "", 0

    # Merge RAG/system context — server-side RAG search first, then live web
    # results, then any caller-supplied context_override (used by the
    # Telegram bot to ground the LLM with its own RAG search results).
    system_context = user_context
    if rag_context:
        extra = f"RAG Context:\n{rag_context.strip()}"
        system_context = f"{system_context}\n\n{extra}" if system_context else extra
    if web_context:
        extra = f"Web results (live):\n{web_context.strip()}"
        system_context = f"{system_context}\n\n{extra}" if system_context else extra
    if client_system_context:
        extra = f"RAG Context:\n{client_system_context.strip()}"
        system_context = f"{system_context}\n\n{extra}" if system_context else extra
    # Live tool data goes LAST so the LLM grounds its answer in the real
    # number over any RAG/web context (most authoritative, most recent).
    if tool_result:
        system_context = _inject_tool_result(system_context, tool_result)

    messages = await _build_messages(user_id, req.message, system_context, req.history)

    # Save user message (sanitized)
    _save_message(user_id, "user", req.message)

    try:
        # Complexity-based routing: analytical/forensic questions go to the
        # stronger deep (long) tier, simple/quick ones to the fast (snappy)
        # tier. An explicit req.model wins over routing.
        task_type = "chat" if req.model else _route_task(req.message)
        result = await do_chat_completion(
            messages=messages,
            model=req.model,
            task_type=task_type,
            tier=req.tier,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as e:
        raise ExternalAPIError(message=f"AI provider error: {e}") from e

    if "error" in result:
        raise ExternalAPIError(message=result["error"])

    # ── SECURITY LAYER 3: Output sanitization ──
    response_content = ai_guard.sanitize_output(result.get("content", ""))
    if scan.details.get("disclaimer") and DISCLAIMER not in response_content:
        response_content += f"\n\n{DISCLAIMER}"

    # ── Cache the (sanitized) success response ──
    # Grounded requests are cached under the grounding-scoped key (same
    # persona + same message → hit; different persona → miss). Market-ish
    # queries (price/mcap/chart/now/today) get a short TTL because their
    # answers go stale fast; everything else an hour (the persona changes
    # daily, so 1h is well within the fresh-grounding window). This runs only
    # on success, so failed calls are never cached.
    ttl = _CACHE_TTL_MARKET if _is_market_query(req.message) else _CACHE_TTL_DEFAULT
    _cache_set(cache_key, response_content, ttl)

    _apply_rate_limit_headers(response, request)

    # Expose how many RAG hits grounded this answer (0 = RAG unavailable or
    # no relevant context) so the frontend can show "Powered by RAG".
    response.headers["X-RAG-Context"] = str(rag_count)
    # Expose how many live web results were included (only set when > 0).
    if web_count:
        response.headers["X-Web-Context"] = str(web_count)

    # Save assistant response
    metadata = {
        "model": result.get("model", ""),
        "provider": result.get("_provider", ""),
        "latency_ms": result.get("_latency_ms", 0),
        "security_scanned": True,
        "rag_context_count": rag_count,
        "web_context_count": web_count,
    }
    _save_message(user_id, "assistant", response_content, metadata)

    return {
        "id": f"resp-{uuid.uuid4().hex[:8]}",
        "message": response_content,
        "model": result.get("model", ""),
        "provider": result.get("_provider", ""),
        "latency_ms": result.get("_latency_ms", 0),
        "usage": result.get("usage", {}),
        # Honest RAG grounding count (real unique hits, not collections x top_k).
        "x_rag_context": rag_count,
    }


@router.post("/stream")
async def chat_stream(
    req: StreamRequest,
    request: Request,
    user: dict[str, Any] | None = Depends(get_current_user),
    _: None = Depends(chat_rate_limit),
) -> Any | None:
    """Streaming chat endpoint using Server-Sent Events (SSE)."""
    user_id = user.get("id", "anon") if user else "anon"

    # ── SECURITY LAYER 1: Input sanitization ──
    raw_message = req.message
    sanitized = ai_guard.sanitize_input(raw_message)
    if sanitized != raw_message:
        req.message = sanitized

    # ── SECURITY LAYER 2: Attack vector scan ──
    scan = ai_guard.scan_input(req.message, session_id=user_id)
    if not scan.safe:
        ai_guard.record_block(
            user_id, message=req.message, reason=scan.reason, category=scan.details.get("category", "")
        )
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'error', 'content': f'Security alert: {scan.reason}'})}\n\n"]),
            media_type="text/event-stream",
        )

    # Save user message (sanitized)
    _save_message(user_id, "user", req.message)

    # ── RAG grounding (non-blocking) ──
    # Same best-effort RAG context injection as the non-streaming endpoint;
    # on failure/timeout this returns empty and the stream proceeds without
    # RAG context.
    try:
        rag_context, rag_count = await _search_rag_context(req.message)
    except Exception as e:  # defensive: never let RAG break chat
        logger.debug("RAG context search raised in stream handler: %s", e)
        rag_context, rag_count = "", 0
    system_context = f"RAG Context:\n{rag_context.strip()}" if rag_context else None

    messages = await _build_messages(user_id, req.message, system_context)

    async def event_generator() -> AsyncGenerator[str, None]:
        full_response = ""
        start_time = time.time()

        try:
            async for token in do_stream_chat_completion(
                messages=messages,
                model=req.model,
                tier=req.tier,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                if token.startswith("[ERROR"):
                    yield f"data: {json.dumps({'type': 'error', 'content': token})}\n\n"
                    return

                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            return

        # ── SECURITY LAYER 3: Output sanitization ──
        safe_response = ai_guard.sanitize_output(full_response)
        if scan.details.get("disclaimer") and DISCLAIMER not in safe_response:
            disclaimer_note = f"\n\n{DISCLAIMER}"
            safe_response += disclaimer_note
            yield f"data: {json.dumps({'type': 'token', 'content': disclaimer_note})}\n\n"

        # Save full response (sanitized)
        latency_ms = (time.time() - start_time) * 1000
        _save_message(
            user_id,
            "assistant",
            safe_response,
            {
                "latency_ms": round(latency_ms, 1),
                "security_scanned": True,
                "rag_context_count": rag_count,
            },
        )

        yield f"data: {json.dumps({'type': 'done', 'latency_ms': round(latency_ms, 1)})}\n\n"

    stream_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        # Expose how many RAG hits grounded this stream (0 = none).
        "X-RAG-Context": str(rag_count),
    }
    rl_headers = getattr(request.state, "chat_rate_limit_headers", None)
    if rl_headers:
        stream_headers.update({k: str(v) for k, v in rl_headers.items()})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=stream_headers,
    )
