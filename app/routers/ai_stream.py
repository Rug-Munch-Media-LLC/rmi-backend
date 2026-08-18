"""
SSE Response Streaming — RAG-powered AI endpoints.

Replaces the old basic LLM pass-through with a full RAG-augmented chat:
tool detection → RAG search → web search → context assembly → LLM → citations.

All endpoints use the backup OpenRouter account (OPENROUTER_BACKUP_API_KEY)
as configured in app/llm_config.py.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import llm_config
from app.core.logging import get_logger
from app.rag.chat_integration import chat_with_context, direct_chat

logger = get_logger("ai_stream")
router = APIRouter(prefix="/api/v1/ai", tags=["ai-streaming"])


class StreamRequest(BaseModel):
    prompt: str
    task: str = "fast"  # fast|simple|complex|chat
    prefer: str = "fast"
    disable_rag: bool = False  # Set true to skip RAG/web search for speed


def _tier_for(task: str) -> str:
    """Map task type to OpenRouter tier.

    - "fast"/"simple" → "snappy" (Laguna S 2.1, sub-300ms)
    - "complex" → "long" (Nemotron Ultra 550B, deep reasoning)
    - "chat" → "long" (general chat with context, quality matters)
    """
    return "long" if task in ("complex", "chat") else "snappy"


def _model_for(tier: str) -> str:
    return str(llm_config.get_long_config() if tier == "long" else llm_config.get_snappy_config()).get("model") or tier


def _key_missing() -> bool:
    return not (llm_config.get_snappy_config().get("api_key") or "")


async def _stream_response(prompt: str, task: str, disable_rag: bool = False) -> Any:
    """Stream a RAG-augmented chat response.

    Emits SSE chunks: ``data: {"token": ...}`` → ``data: {"sources": [...]}``
    → ``data: {"done": true, "model": ...}``. Fails open on errors.
    """
    tier = _tier_for(task)
    model = _model_for(tier)

    if _key_missing():
        yield f"data: {json.dumps({'error': 'OpenRouter key missing'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"
        return

    # Detect tool intent (price/scan/news/market etc.)
    from app.domains.chat_tools._intent import _detect_tool_intent
    from app.domains.chat_tools.dispatcher import _run_tool_for_intent

    intent = _detect_tool_intent(prompt) if not disable_rag else None
    tool_result = await _run_tool_for_intent(prompt, intent) if intent else None

    if disable_rag and not tool_result:
        # Fast path: no context, no tools
        result = await direct_chat(prompt, tier=tier)
    else:
        # Full RAG path
        result = await chat_with_context(
            prompt,
            tool_result=tool_result,
            intent=intent,
            tier="long" if tool_result else tier,  # Use long tier for tool results
        )

    answer = result.get("answer", "")
    sources = result.get("sources", [])

    if not answer:
        yield f"data: {json.dumps({'error': 'All providers failed'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"
        return

    # Stream tokens
    for word in answer.split():
        yield f"data: {json.dumps({'token': word + ' '})}\n\n"
        await asyncio.sleep(0.02)

    # Stream sources after the answer
    if sources:
        yield f"data: {json.dumps({'sources': sources})}\n\n"

    yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"


@router.post("/stream")
async def stream_ai(req: StreamRequest) -> Any:
    """Stream AI response with RAG context, tool results, and web search."""
    tier = _tier_for(req.task)
    model = _model_for(tier)
    return StreamingResponse(
        _stream_response(req.prompt, req.task, req.disable_rag),
        media_type="text/event-stream",
        headers={
            "X-Model": model,
            "X-Provider": "openrouter",
            "X-RAG": "enabled" if not req.disable_rag else "disabled",
            "X-Latency-Ms": "300" if tier == "snappy" else "500",
        },
    )


@router.post("/route")
async def ai_route(req: StreamRequest) -> dict[str, Any]:
    """Non-streaming RAG-augmented chat. Returns complete response with sources."""
    if _key_missing():
        return {"error": "OpenRouter key missing", "tier": _tier_for(req.task)}

    intent = _detect_tool_intent(req.prompt) if not req.disable_rag else None
    tool_result = await _run_tool_for_intent(req.prompt, intent) if intent else None

    if req.disable_rag and not tool_result:
        result = await direct_chat(req.prompt, tier=_tier_for(req.task))
    else:
        result = await chat_with_context(
            req.prompt,
            tool_result=tool_result,
            intent=intent,
            tier="long" if tool_result else _tier_for(req.task),
        )

    return result


# ── Health / Info ──


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    """List available AI providers with capabilities."""
    snappy = llm_config.get_snappy_config()
    long = llm_config.get_long_config()
    return {
        "providers": {
            "fast": [{"model": snappy["model"], "provider": "openrouter", "latency_ms": 300, "cost": 0}],
            "complex": [{"model": long["model"], "provider": "openrouter", "latency_ms": 500, "cost": 0}],
            "chat": [
                {"model": long["model"], "provider": "openrouter", "rag": True, "web_search": True, "tools": True}
            ],
        }
    }
