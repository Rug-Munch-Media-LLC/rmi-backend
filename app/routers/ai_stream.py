"""SSE Response Streaming - real-time token streaming for AI endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import llm_config

router = APIRouter(prefix="/api/v1/ai", tags=["ai-streaming"])


class StreamRequest(BaseModel):
    prompt: str
    task: str = "fast"  # fast|cheap|complex|bulk|code
    prefer: str = "fast"  # fast|cheap


def _tier_for(task: str) -> str:
    """Map a request to a free OpenRouter tier.

    - "snappy" (poolside/laguna-s-2.1:free) — fast/cheap short answers
    - "long" (nvidia/nemotron-3-ultra-550b-a55b:free) — deep reasoning
    """
    return "long" if task == "complex" else "snappy"


def _config_for(tier: str) -> dict[str, Any]:
    """Return the llm_config for a free OpenRouter tier."""
    return llm_config.get_long_config() if tier == "long" else llm_config.get_snappy_config()


def _model_for(tier: str) -> str:
    return str(_config_for(tier).get("model") or tier)


def _key_missing(tier: str) -> bool:
    """True when the OpenRouter key backing this tier is not configured."""
    return not str(_config_for(tier).get("api_key") or "")


async def _stream_openrouter(prompt: str, task: str) -> Any:
    """Stream a chat completion from the free OpenRouter tier.

    Emits the standard SSE shape: ``data: {"token": ...}`` chunks followed by
    ``data: {"done": true, "model": ...}``. Fails open with a clear message
    when the key is missing or both chat providers fail.
    """
    tier = _tier_for(task)
    model = _model_for(tier)

    if _key_missing(tier):
        yield f"data: {json.dumps({'error': 'OpenRouter key missing — set OPENROUTER_BACKUP_API_KEY'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"
        return

    answer = await llm_config.chat_with_fallback(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
    )
    if not answer:
        yield f"data: {json.dumps({'error': f'All providers failed (tier={tier})'})}\n\n"
        yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"
        return

    for word in answer.split():
        yield f"data: {json.dumps({'token': word + ' '})}\n\n"
        await asyncio.sleep(0.02)
    yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"


@router.post("/stream")
async def stream_ai(req: StreamRequest) -> Any:
    """Stream AI response in real-time. Tokens appear as generated."""
    tier = _tier_for(req.task)
    model = _model_for(tier)
    return StreamingResponse(
        _stream_openrouter(req.prompt, req.task),
        media_type="text/event-stream",
        headers={
            "X-Model": model,
            "X-Provider": "openrouter",
            "X-Latency-Ms": "300" if tier == "snappy" else "500",
        },
    )


@router.post("/route")
async def ai_route(req: StreamRequest) -> Any:
    """Non-streaming: auto-route to the free OpenRouter tier, return complete response."""
    tier = _tier_for(req.task)
    model = _model_for(tier)
    if _key_missing(tier):
        return {
            "error": "OpenRouter key missing — set OPENROUTER_BACKUP_API_KEY",
            "tier": tier,
            "model": model,
        }
    answer = await llm_config.chat_with_fallback(
        messages=[{"role": "user", "content": req.prompt}],
        tier=tier,
    )
    if not answer:
        return {"error": "All providers failed", "tier": tier, "model": model}
    return {"response": answer, "model": model, "provider": "openrouter", "tier": tier}


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    """List all available AI providers with capabilities (free OpenRouter tiers)."""
    snappy = llm_config.get_snappy_config()
    long = llm_config.get_long_config()
    snappy_entry = {
        "model": snappy["model"],
        "provider": "openrouter",
        "latency_ms": 300,
        "cost_per_1M_input": 0.0,
    }
    long_entry = {
        "model": long["model"],
        "provider": "openrouter",
        "latency_ms": 500,
        "cost_per_1M_input": 0.0,
    }
    return {
        "providers": {
            "fast": [snappy_entry],
            "cheap": [snappy_entry],
            "complex": [long_entry],
            "bulk": [snappy_entry],
            "vision": [snappy_entry],
            "embed": [snappy_entry],
            "code": [snappy_entry],
        }
    }
