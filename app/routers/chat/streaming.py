"""Streaming (SSE) chat route.

Moved from ``app/routers/chat/__init__.py`` (de-godding, behavior-preserving).

PATCHING CONTRACT: tests patch ``app.routers.chat.do_stream_chat_completion``
and ``_search_rag_context`` on the PACKAGE module. The call sites below
resolve those names through the package module (``chat_pkg``) at call time
instead of binding them at import time.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator  # noqa: TC003  (runtime annotation use)
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.ai_router import (
    stream_chat_completion as do_stream_chat_completion,  # noqa: F401  (re-export: tests patch via package)
)
from app.auth import get_current_user
from app.core.logging import get_logger

from ._messages import _build_messages, ai_guard
from ._persistence import _save_message
from ._prompts import DISCLAIMER
from .models import StreamRequest  # noqa: TC001  (runtime: FastAPI resolves via get_type_hints)

logger = get_logger(__name__)

# Package module — patched attributes (see docstring) resolve at call time.
from app.routers import chat as chat_pkg  # noqa: E402  (circular by design)


def register_routes(router: APIRouter) -> None:
    """Attach the streaming chat route to ``router``."""

    @router.post("/stream")
    async def chat_stream(
        req: StreamRequest,
        request: Request,
        user: dict[str, Any] | None = Depends(get_current_user),
        _: None = Depends(chat_pkg.chat_rate_limit),
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
            rag_context, rag_count = await chat_pkg._search_rag_context(req.message)
        except Exception as e:  # defensive: never let RAG break chat
            logger.debug("RAG context search raised in stream handler: %s", e)
            rag_context, rag_count = "", 0
        system_context = f"RAG Context:\n{rag_context.strip()}" if rag_context else None

        messages = await _build_messages(user_id, req.message, system_context)

        async def event_generator() -> AsyncGenerator[str, None]:
            full_response = ""
            start_time = time.time()

            try:
                async for token in chat_pkg.do_stream_chat_completion(
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
