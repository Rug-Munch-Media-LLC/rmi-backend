"""Non-streaming chat route + composed handler steps.

Moved from ``app/routers/chat/__init__.py`` (de-godding, behavior-preserving).

PATCHING CONTRACT: tests patch ``app.routers.chat.do_chat_completion`` /
``searxng_search`` / ``_search_rag_context`` / ``_fetch_tool_context`` on the
PACKAGE module. The call sites below resolve those names through the package
module (``chat_pkg``) at call time instead of binding them at import time.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.ai_router import chat_completion as do_chat_completion  # noqa: F401  (re-export: tests patch via package)
from app.auth import get_current_user
from app.core.errors import AuthorizationError, ExternalAPIError
from app.core.logging import get_logger
from app.core.web_search import searxng_search  # noqa: F401  (re-export: tests patch via package)

from ._messages import _build_messages, ai_guard
from ._persistence import _save_message
from ._prompts import DISCLAIMER, _identity_answer, _should_web_search, _vague_question
from ._rag import (
    _CACHE_TTL_DEFAULT,
    _CACHE_TTL_MARKET,
    _cache_key,
    _format_web_context,
    _inject_tool_result,
    _is_market_query,
    _route_task,
)
from ._response_cache import _cache_get, _cache_set
from .models import ChatRequest  # noqa: TC001  (runtime: FastAPI resolves via get_type_hints)

logger = get_logger(__name__)

# Package module — patched attributes (see docstring) resolve at call time.
from app.routers import chat as chat_pkg  # noqa: E402  (circular by design)


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
    # Resolve through the package module so tests patching
    # ``app.routers.chat.get_chat_rate_limiter`` are honored.
    limiter = chat_pkg.get_chat_rate_limiter()
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


def _respond_canned(
    user_id: str,
    message: str,
    answer: str,
    *,
    model: str,
    provider: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Persist both turns and return a canned (non-LLM) response payload.

    Shared by the identity / vague-question / response-cache fast paths:
    no RAG, no web search, no LLM round-trip — but history is still saved
    so the conversation stays consistent for follow-ups.
    """
    _save_message(user_id, "user", message)
    _save_message(
        user_id,
        "assistant",
        answer,
        {"model": model, "provider": provider, "security_scanned": True, "rag_context_count": 0},
    )
    _apply_rate_limit_headers(response, request)
    response.headers["X-RAG-Context"] = "0"
    return {
        "id": f"resp-{uuid.uuid4().hex[:8]}",
        "message": answer,
        "model": model,
        "provider": provider,
        "latency_ms": 0,
        "usage": {},
        "x_rag_context": 0,
    }


async def _gather_grounding(req: ChatRequest, user_context: str) -> tuple[str, int, int]:
    """Fetch tool/RAG/web grounding concurrently and merge the system context.

    Returns ``(system_context, rag_count, web_count)``. Grounding order in
    the merged context: server-side RAG first, then live web results, then
    any caller-supplied ``context_override.system_context``, with live tool
    data LAST (most authoritative). Best-effort end to end: every fetcher is
    fail-open, so a tool/RAG/web failure degrades to empty context and chat
    proceeds.
    """
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
            return await chat_pkg._fetch_tool_context(req.message)
        except Exception as e:  # defensive: never let the tool layer break chat
            logger.debug("chat tool fetch raised in handler: %s", e)
            return None

    async def _safe_rag_fetch() -> tuple[str, int]:
        try:
            return await chat_pkg._search_rag_context(req.message)
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
                web_results = await chat_pkg.searxng_search(req.message)
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
            rag_context, rag_count = await chat_pkg._search_rag_context(req.message)
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

    return system_context, rag_count, web_count


async def _run_chat_completion(req: ChatRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Complexity-routed LLM call. Raises ExternalAPIError on provider failure.

    Analytical/forensic questions go to the stronger deep (long) tier,
    simple/quick ones to the fast (snappy) tier. An explicit ``req.model``
    wins over routing.
    """
    try:
        task_type = "chat" if req.model else _route_task(req.message)
        result = await chat_pkg.do_chat_completion(
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
    return result


def _finalize_chat_response(
    *,
    user_id: str,
    req: ChatRequest,
    request: Request,
    response: Response,
    result: dict[str, Any],
    scan: Any,
    cache_key: str,
    rag_count: int,
    web_count: int,
) -> dict[str, Any]:
    """Sanitize output, cache it, set grounding headers, persist, build payload.

    SECURITY LAYER 3 (output sanitization) + the response cache write run
    only on success, so failed calls are never cached. Grounded requests are
    cached under the grounding-scoped key (same persona + same message → hit;
    different persona → miss). Market-ish queries (price/mcap/chart/now/
    today) get a short TTL because their answers go stale fast; everything
    else an hour (the persona changes daily, so 1h is well within the
    fresh-grounding window).
    """
    response_content = ai_guard.sanitize_output(result.get("content", ""))
    if scan.details.get("disclaimer") and DISCLAIMER not in response_content:
        response_content += f"\n\n{DISCLAIMER}"

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


def register_routes(router: APIRouter) -> None:
    """Attach the non-streaming chat route to ``router``."""

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
        # search, no LLM round-trip (see _respond_canned).
        identity_answer = _identity_answer(req.message)
        if identity_answer is not None:
            return _respond_canned(
                user_id,
                req.message,
                identity_answer,
                model="canned",
                provider="identity",
                request=request,
                response=response,
            )

        # ── VAGUE-QUESTION FAST-PATH ──
        # Underspecified questions ("what happened with the one token the other
        # day") get a clarification prompt — no RAG/web/LLM round-trip, no risk
        # of a degenerate "- " answer from the model.
        vague_answer = _vague_question(req.message)
        if vague_answer is not None:
            return _respond_canned(
                user_id, req.message, vague_answer, model="canned", provider="vague", request=request, response=response
            )

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
        # store after a successful LLM response in _finalize_chat_response).
        client_grounded = bool(
            req.context_override
            and isinstance(req.context_override, dict)
            and isinstance(req.context_override.get("system_context"), str)
            and req.context_override.get("system_context", "").strip()
        )
        grounding_text = (
            req.context_override.get("system_context") if client_grounded and req.context_override else None
        )
        ck = _cache_key(req.message, grounding=grounding_text)
        cached = _cache_get(ck)
        if cached is not None:
            return _respond_canned(
                user_id, req.message, cached, model="cached", provider="cache", request=request, response=response
            )

        # Get or create user context
        user_context = ""
        if user:
            tier = user.get("tier", "free")
            display = user.get("display_name", user.get("email", "detective"))
            user_context = f"User: {display} (tier: {tier})"

        # ── GROUNDING: tool-use (live data) + RAG + live web search ──
        # If the caller already supplied explicit system context (e.g. the
        # Telegram bot sends its own RAG search via context_override.
        # system_context), respect that instead — no double-grounding.
        system_context, rag_count, web_count = await _gather_grounding(req, user_context)

        messages = await _build_messages(user_id, req.message, system_context, req.history)

        # Save user message (sanitized)
        _save_message(user_id, "user", req.message)

        # ── MODEL CALL (complexity-routed) ──
        result = await _run_chat_completion(req, messages)

        # ── SECURITY LAYER 3 + cache write + headers + persistence + payload ──
        return _finalize_chat_response(
            user_id=user_id,
            req=req,
            request=request,
            response=response,
            result=result,
            scan=scan,
            cache_key=ck,
            rag_count=rag_count,
            web_count=web_count,
        )
