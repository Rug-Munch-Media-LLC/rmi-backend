"""Orchestration for the canonical chat endpoint — retrieve, compose, degrade.

Guarantees a 200 with an answer: a model failure (or the
``CHAT_PROVIDERS_DISABLED=1`` kill switch) degrades to the retrieval-only
answer built from the top two passages; missing evidence degrades to an
explicit ``insufficient_evidence`` refusal.
"""

from __future__ import annotations

import asyncio
import os
import time

from app.core.logging import get_logger
from app.domains.chat import compose, ports
from app.domains.chat.memory import ConversationMemory
from app.domains.chat.retrieval import Passage, retrieve
from app.domains.chat.schemas import ChatRequest, ChatResponse
from app.domains.chat.web import search_and_extract

log = get_logger(__name__)

TOTAL_TIMEOUT_S = 45.0
TOP_K = 6
RERANK_MIN_SCORE = float(os.getenv("CHAT_RERANK_MIN_SCORE", "0.2"))

_memory = ConversationMemory()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _degraded(
    started: float, passages: list[Passage], reason: str, web_used: bool
) -> ChatResponse:
    text, citations = compose.retrieval_only(passages, reason)
    log.warning(
        "chat_degraded reason=%s passages=%d latency_ms=%d",
        reason,
        len(passages),
        _ms(started),
    )
    return ChatResponse(
        answer=text,
        citations=citations,
        provider="retrieval_only",
        fallback_used=True,
        latency_ms=_ms(started),
        refused=not citations,
        refusal_reason=None if citations else "insufficient_evidence",
    )


async def _evidence(query: str) -> tuple[list[Passage], bool]:
    """RAG first; web only when the reranked top score is below the floor."""
    passages = await retrieve(query, top_k=TOP_K)
    if passages and passages[0].score >= RERANK_MIN_SCORE:
        return passages, False
    log.info("chat_rag_weak top_score=%.3f", passages[0].score if passages else 0.0)
    web_passages = await search_and_extract(query)
    if web_passages:
        return web_passages, True
    return [], False


async def _draft(
    req: ChatRequest, passages: list[Passage], history: list[dict[str, str]]
) -> tuple[str, str, str | None] | None:
    """Return ``(text, provider, model)`` or ``None`` when no draft exists."""
    system = ports.persona_prompt(req.persona)
    messages = compose.build_messages(system, passages, req.message, history)
    for attempt in range(2):
        result = await ports.complete(messages)
        if result is None:
            return None
        text = result.text.strip()
        ok, reason = compose.validate_answer(text, len(passages))
        if ok:
            return text, result.provider, result.model
        if reason == "too_long":
            trimmed = compose._truncate_words(text)
            if compose.validate_answer(trimmed, len(passages))[0]:
                return trimmed, result.provider, result.model
        log.warning("chat_draft_rejected attempt=%d reason=%s", attempt + 1, reason)
        if attempt == 0:
            messages = [*messages, compose.stricter_nudge()]
    return None


async def _run(req: ChatRequest, started: float) -> ChatResponse:
    history = await _memory.history(req.conversation_id)
    passages, web_used = await _evidence(req.message)
    if not passages:
        text, citations = compose.refusal()
        return ChatResponse(
            answer=text,
            citations=citations,
            provider="retrieval_only",
            fallback_used=True,
            latency_ms=_ms(started),
            refused=True,
            refusal_reason="insufficient_evidence",
        )
    draft = await _draft(req, passages, history)
    if draft is None:
        return _degraded(started, passages, "model_unavailable", web_used)
    text, provider, model = draft
    response = ChatResponse(
        answer=text,
        citations=compose.to_citations(passages),
        provider=provider,
        model=model,
        fallback_used=web_used,
        latency_ms=_ms(started),
    )
    await _memory.append(req.conversation_id, req.message, text)
    log.info(
        "chat_answered persona=%s channel=%s provider=%s citations=%d latency_ms=%d",
        req.persona,
        req.channel,
        provider,
        len(response.citations),
        response.latency_ms,
    )
    return response


async def answer(req: ChatRequest) -> ChatResponse:
    """Always returns a :class:`ChatResponse` — never raises."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            return await _run(req, started)
    except TimeoutError:
        log.warning("chat_timeout budget_s=%s", TOTAL_TIMEOUT_S)
        return _degraded(started, [], "timeout", False)
    except Exception as exc:
        log.error("chat_unhandled err=%s", type(exc).__name__)
        return _degraded(started, [], "error", False)
