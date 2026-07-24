"""SSE Response Streaming - real-time token streaming for AI endpoints."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.core.model_router import TaskType, route_task
from typing import Any

router = APIRouter(prefix="/api/v1/ai", tags=["ai-streaming"])

class StreamRequest(BaseModel):
    prompt: str
    task: str = "fast"  # fast|cheap|complex|bulk|code
    prefer: str = "fast"  # fast|cheap

async def _stream_ollama(prompt: str, model: str) -> Any:
    """Stream from Ollama."""
    import httpx

    async with (
        httpx.AsyncClient(timeout=120) as c,
        c.stream(
            "POST", f"{settings.ollama_url}/api/generate", json={"model": model, "prompt": prompt}
        ) as r,
    ):
        async for line in r.aiter_lines():
            if line:
                try:
                    chunk = json.loads(line)
                    if chunk.get("done"):
                        yield f"data: {json.dumps({'done': True, 'model': model})}\n\n"
                        break
                    yield f"data: {json.dumps({'token': chunk.get('response', '')})}\n\n"
                except json.JSONDecodeError:
                    continue

@router.post("/stream")
async def stream_ai(req: StreamRequest) -> Any:
    """Stream AI response in real-time. Tokens appear as generated."""
    decision = route_task(TaskType(req.task), req.prefer)

    if decision.provider == "ollama":
        return StreamingResponse(
            _stream_ollama(req.prompt, decision.model),
            media_type="text/event-stream",
            headers={
                "X-Model": decision.model,
                "X-Provider": decision.provider,
                "X-Latency-Ms": str(decision.estimated_latency_ms),
            },
        )

    # For non-Ollama providers, fall back to blocking + stream result
    async def _blocking_stream() -> Any:
        from app.core.model_router import smart_route

        result = await smart_route(req.prompt, req.task, req.prefer)
        if result and "response" in result:
            words = result["response"].split()
            for word in words:
                yield f"data: {json.dumps({'token': word + ' '})}\n\n"
                await asyncio.sleep(0.05)
        yield f"data: {json.dumps({'done': True, 'model': decision.model})}\n\n"

    return StreamingResponse(
        _blocking_stream(),
        media_type="text/event-stream",
        headers={"X-Model": decision.model, "X-Provider": decision.provider},
    )

@router.post("/route")
async def ai_route(req: StreamRequest) -> Any:
    """Non-streaming: auto-route to best model, return complete response."""
    from app.core.model_router import smart_route

    result = await smart_route(req.prompt, req.task, req.prefer)
    return result if result else {"error": "All providers failed"}

@router.get("/providers")
async def list_providers() -> dict[str, dict]:
    """List all available AI providers with capabilities."""
    from app.core.model_router import ROUTING_TABLE

    return {
        "providers": {
            task.value: [
                {
                    "model": m[0],
                    "provider": m[1],
                    "latency_ms": m[2] * 1000,
                    "cost_per_1M_input": m[3],
                }
                for m in models
            ]
            for task, models in ROUTING_TABLE.items()
        }
    }
