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

Layout (de-godding phase 4): the package is split into sub-modules —
``models`` (Pydantic request models), ``rag_integration`` (RAG + tool
grounding), ``routes`` (non-streaming handler + composed steps) and
``streaming`` (SSE handler). Every public name is re-exported here so
existing importers and tests keep working against the SAME objects.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import APIRouter

from app.ai_router import chat_completion as do_chat_completion  # noqa: F401  (re-export: tests patch via package)
from app.core.logging import get_logger
from app.core.web_search import searxng_search  # noqa: F401  (re-export: tests patch via package)
from app.domains.chat_tools import (  # noqa: F401  (re-export: tests patch via package)
    _detect_tool_intent,
    _run_tool_for_intent,
)
from app.domains.chat_tools.market_fetch import (
    COINGECKO_API,  # noqa: F401  (re-export for existing importers)
    _fetch_market_context,  # noqa: F401  (patch target: app.routers.chat._fetch_market_context)
    _get_client,  # noqa: F401  (re-export for existing importers)
)
from app.security.chat_guardrails import get_chat_rate_limiter  # noqa: F401  (re-export: tests patch via package)

from ..chat_history import router as chat_history_router

# ── Extracted pure helpers ──
# Message-building + shared ai_guard live in ._messages; prompt text and the
# fast-path detectors (identity / vague / web-search) live in ._prompts.
# Names are re-exported here so every existing importer and test keeps
# working against the SAME objects (no behavior change).
from ._messages import (
    CONTEXT_MAX_MESSAGES,  # noqa: F401  (re-export for existing importers)
    _build_messages,  # noqa: F401  (re-export for existing importers)
    _normalize_history,  # noqa: F401  (re-export for existing importers)
    _sanitize_history,  # noqa: F401  (re-export for existing importers)
    ai_guard,  # noqa: F401  (re-export for existing importers)
)
from ._persistence import _save_message  # noqa: F401  (re-export for existing importers)
from ._prompts import (
    _EVM_ADDRESS_RE,  # noqa: F401  (re-export for existing importers)
    _IDENTITY_ANSWER,  # noqa: F401  (re-export for existing importers)
    _IDENTITY_RE,  # noqa: F401  (re-export for existing importers)
    _SOLANA_ADDRESS_RE,  # noqa: F401  (re-export for existing importers)
    _SYMBOL_RE,  # noqa: F401  (re-export for existing importers)
    _VAGUE_REFERENTS,  # noqa: F401  (re-export for existing importers)
    DISCLAIMER,  # noqa: F401  (re-export for existing importers)
    SYSTEM_PROMPT,  # noqa: F401  (re-export for existing importers)
    _identity_answer,  # noqa: F401  (re-export for existing importers)
    _looks_like_current_events,  # noqa: F401  (re-export for existing importers)
    _should_web_search,  # noqa: F401  (re-export for existing importers)
    _vague_question,  # noqa: F401  (re-export for existing importers)
)
from ._rag import (
    _CACHE_MAX_ENTRIES,  # noqa: F401  (re-export for existing importers)
    _CACHE_TTL_DEFAULT,  # noqa: F401  (re-export for existing importers)
    _CACHE_TTL_MARKET,  # noqa: F401  (re-export for existing importers)
    _MAX_TOKEN_HINTS,  # noqa: F401  (re-export for existing importers)
    RAG_COLLECTIONS,  # noqa: F401  (re-export for existing importers)
    RAG_CONTEXT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    RAG_HIT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    RAG_SEARCH_TIMEOUT,  # noqa: F401  (re-export for existing importers)
    RAG_TOKEN_COLLECTIONS,  # noqa: F401  (re-export for existing importers)
    RAG_TOP_K,  # noqa: F401  (re-export for existing importers)
    TOOL_RESULT_LABEL,  # noqa: F401  (re-export for existing importers)
    WEB_CONTEXT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    WEB_HIT_MAX_CHARS,  # noqa: F401  (re-export for existing importers)
    _build_rag_query,  # noqa: F401  (re-export for existing importers)
    _cache_key,  # noqa: F401  (re-export for existing importers)
    _extract_token_hints,  # noqa: F401  (re-export for existing importers)
    _format_rag_context,  # noqa: F401  (re-export for existing importers)
    _format_web_context,  # noqa: F401  (re-export for existing importers)
    _inject_tool_result,  # noqa: F401  (re-export for existing importers)
    _is_market_query,  # noqa: F401  (re-export for existing importers)
    _rag_dedup_key,  # noqa: F401  (re-export for existing importers)
    _route_task,  # noqa: F401  (re-export for existing importers)
)
from ._response_cache import (
    _cache_get,  # noqa: F401  (re-export for existing importers)
    _cache_set,  # noqa: F401  (re-export for existing importers)
    _chat_cache,  # noqa: F401  (module attribute — tests clear it directly)
)

# ── Phase-2 de-godding extractions (2026-08-21) ──
# Request models + token budget, response cache, persistence, and the market
# context fetcher live in their own modules; names are re-exported here so
# every existing importer and test keeps working against the SAME objects.
from .models import (
    _CHAT_MAX_TOKENS_BASE,  # noqa: F401  (re-export for existing importers)
    _CHAT_MAX_TOKENS_MAX,  # noqa: F401  (re-export for existing importers)
    _CHAT_MAX_TOKENS_MIN,  # noqa: F401  (re-export for existing importers)
    CHAT_MAX_TOKENS_DEFAULT,  # noqa: F401  (re-export for existing importers)
    ChatRequest,
    StreamRequest,
    _resolve_chat_max_tokens_default,  # noqa: F401  (re-export for existing importers)
)

# ── Phase-4 de-godding split (2026-09-05) ──
# RAG/tool grounding lives in .rag_integration; the non-streaming handler and
# its composed steps in .routes; the SSE handler in .streaming. All names are
# re-exported below so existing importers and tests keep working against the
# SAME objects (the sub-modules resolve test-patched attributes through this
# package module at call time — see their PATCHING CONTRACT docstrings).
from .rag_integration import (
    _RAG_MEMO_TTL,  # noqa: F401  (re-export for existing importers)
    _fetch_tool_context,  # noqa: F401  (re-export for existing importers)
    _rag_memo,  # noqa: F401  (module attribute — per-turn memo state)
    _rag_memo_get,  # noqa: F401  (re-export for existing importers)
    _rag_memo_set,  # noqa: F401  (re-export for existing importers)
    _rag_search_collection,  # noqa: F401  (re-export for existing importers)
    _search_rag_context,  # noqa: F401  (re-export for existing importers)
)
from .routes import (
    _apply_rate_limit_headers,  # noqa: F401  (re-export for existing importers)
    _client_ip,  # noqa: F401  (re-export for existing importers)
    _finalize_chat_response,  # noqa: F401  (re-export for existing importers)
    _gather_grounding,  # noqa: F401  (re-export for existing importers)
    _respond_canned,  # noqa: F401  (re-export for existing importers)
    _run_chat_completion,  # noqa: F401  (re-export for existing importers)
    chat_rate_limit,
)
from .streaming import do_stream_chat_completion  # noqa: F401  (re-export: tests patch via package)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# ── History / stats sub-router (extracted) ──
router.include_router(chat_history_router)

# ── Route registration (sub-modules attach their handlers to ``router``) ──
from .routes import register_routes as _register_chat_routes  # noqa: E402
from .streaming import register_routes as _register_stream_routes  # noqa: E402

_register_chat_routes(router)
_register_stream_routes(router)

if TYPE_CHECKING:
    import redis.asyncio as redis


# ── Redis ──
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

_redis: redis.Redis | None = None

CACHE_TTL = 300  # 5 minutes

# NOTE: COINGECKO_API / _http_client / _get_client moved to
# app/domains/chat_tools/market_fetch.py (re-exported above) — the market
# fetch is shared with the chat tool-use layer, which sits below
# ``app.routers`` (see .importlinter). The orphaned
# ``logger.warning("chat_db_init_failed")`` line that sat unreachable after
# ``return _http_client`` was dropped with the move — it could never execute.

__all__ = [
    "ChatRequest",
    "StreamRequest",
    "chat_rate_limit",
    "router",
]
