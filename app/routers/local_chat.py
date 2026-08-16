"""
Local Model Chat — proxies to free remote OpenAI-compatible providers.
``quick`` mode uses the snappy config (fast short answers), ``deep`` mode
uses the long config (deeper reasoning answers).
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.ai_guardrails import strip_think_tags
from app.core.logging import get_logger
from app.llm_config import get_long_config, get_snappy_config

logger = get_logger("rmi.local_chat")

router = APIRouter(prefix="/api/v1/chat/local", tags=["local-chat"])

# Remote providers are fast — clamp max_tokens to a
# sane ceiling so a client cannot request a huge unbounded generation.
LOCAL_CHAT_MAX_TOKENS = 1024


class LocalChatRequest(BaseModel):
    message: str = Field(..., max_length=2000)
    history: list[dict[str, Any]] = Field(default_factory=list)
    mode: Literal["quick", "deep"] = "quick"
    max_tokens: int = Field(default=1024, ge=64, le=4096)


class LocalChatResponse(BaseModel):
    message: str
    model: str
    provider: str
    mode: str
    latency_ms: int


def _strip_thinking(text: str) -> str:
    """Strip reasoning/thinking blocks (legacy DeepSeek/Qwen), keep only the final answer.

    Think-block removal delegates to the single shared helper
    (``app.core.ai_guardrails.strip_think_tags``); ``<answer>`` wrappers are
    handled here.
    """
    text = strip_think_tags(text)
    text = re.sub(r"</?answer>", "", text)
    return text.strip()


@router.post("", response_model=LocalChatResponse)
async def local_chat(req: LocalChatRequest):
    """Chat with free remote models. Quick mode uses snappy, deep mode uses long."""
    start = time.time()

    if req.mode == "deep":
        cfg = get_long_config()
        timeout = 60.0
    else:
        cfg = get_snappy_config()
        timeout = 30.0

    if not cfg.get("api_key"):
        raise HTTPException(status_code=503, detail="AI provider unavailable: missing API key")

    # Build messages with history
    messages = [
        {
            "role": "system",
            "content": (
                "You are Rug Munch, an AI crypto intelligence assistant. "
                "You help users understand crypto security, detect scams, analyze tokens, "
                "and answer questions about blockchain, DeFi, and crypto markets. "
                "Be direct, witty, slightly cynical but genuinely helpful. "
                "Never give financial advice. Keep answers concise (2-4 sentences). "
                "If you don't know something, say so honestly."
            ),
        }
    ]
    for msg in req.history[-5:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": req.message})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                cfg["base_url"],
                json={
                    "model": cfg["model"],
                    "messages": messages,
                    "max_tokens": min(req.max_tokens, LOCAL_CHAT_MAX_TOKENS),
                    "temperature": cfg.get("temperature", 0.4),
                },
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("remote chat unavailable: %s", e)
        raise HTTPException(status_code=503, detail="AI provider unavailable")

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="AI provider error")

    try:
        raw = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Invalid model response")

    choices = raw.get("choices", [])
    if not choices:
        raise HTTPException(status_code=502, detail="Empty model response")

    answer = choices[0].get("message", {}).get("content", "").strip()

    # Strip thinking output from the deep/long model
    if req.mode == "deep":
        answer = _strip_thinking(answer)

    if not answer:
        raise HTTPException(status_code=502, detail="Empty answer")

    latency_ms = int((time.time() - start) * 1000)

    return LocalChatResponse(
        message=answer,
        model=cfg["model"],
        provider=req.mode,
        mode=req.mode,
        latency_ms=latency_ms,
    )
